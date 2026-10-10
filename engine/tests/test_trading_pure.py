from decimal import Decimal

import pytest

from quantdeck_engine.binance.rest import decimal_param
from quantdeck_engine.trading.order_ids import (
    MAX_LENGTH,
    ClientIdFactory,
    Role,
    parse_client_id,
)
from quantdeck_engine.trading.rules import Side, SymbolRules
from quantdeck_engine.trading.sizing import (
    Bracket,
    EntryRejected,
    emergency_stop_price,
    isolated_liquidation_price,
    max_notional_at,
    risk_amount,
    size_entry,
)

D = Decimal

SYMBOL_INFO = {
    "symbol": "BTCUSDT",
    "marginAsset": "USDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "261.10", "maxPrice": "1"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "120"},
        {"filterType": "MIN_NOTIONAL", "notional": "100"},
    ],
}
RULES = SymbolRules.from_exchange_info(SYMBOL_INFO)

# Shaped like BTCUSDT's lowest brackets; each cum continues the previous one.
BRACKETS = [
    Bracket(D(0), D(50000), D("0.004"), D(0), 125),
    Bracket(D(50000), D(600000), D("0.005"), D(50), 100),
    Bracket(D(600000), D(3000000), D("0.01"), D(3050), 75),
]


def plan(**overrides):
    args = {
        "side": Side.LONG,
        "reference_price": D(60000),
        "stop_price": D(59400),  # 1% away
        "risk_amount": D(50),  # 1% of 5000
        "base_balance": D(5000),
        "max_notional_multiple": D(2),
        "available_balance": D(5000),
        "leverage": 5,
        "rules": RULES,
        "brackets": BRACKETS,
    }
    return size_entry(**{**args, **overrides})


# Client order IDs (D-21)


def test_client_id_format_and_round_trip() -> None:
    ids = ClientIdFactory(clock=lambda: 1759480000.123)
    first = ids.new(Role.STOP, "BTCUSDT")
    assert first == "qd_s_BTCUSDT_1759480000123_00"
    assert ids.new(Role.STOP, "BTCUSDT").endswith("_01")
    parsed = parse_client_id(first)
    assert parsed is not None
    assert (parsed.role, parsed.symbol, parsed.timestamp_ms, parsed.seq) == (
        Role.STOP,
        "BTCUSDT",
        1759480000123,
        0,
    )


def test_client_id_with_underscored_symbol_parses() -> None:
    client_id = ClientIdFactory(clock=lambda: 1759480000.0).new(Role.ENTRY, "BTCUSDT_261225")
    assert len(client_id) <= MAX_LENGTH
    parsed = parse_client_id(client_id)
    assert parsed is not None and parsed.symbol == "BTCUSDT_261225"


def test_client_id_sequence_wraps_at_100() -> None:
    ids = ClientIdFactory(clock=lambda: 1759480000.0)
    for _ in range(100):
        ids.new(Role.ENTRY, "BTCUSDT")
    assert ids.new(Role.ENTRY, "BTCUSDT").endswith("_00")


def test_client_id_too_long_is_refused() -> None:
    with pytest.raises(ValueError, match="36"):
        ClientIdFactory().new(Role.ENTRY, "A" * 20)


@pytest.mark.parametrize(
    "client_id", ["web_abc123", "qd_z_BTCUSDT_1759480000123_00", "qd_e_BTCUSDT_123_00", ""]
)
def test_foreign_client_ids_are_not_parsed(client_id: str) -> None:
    assert parse_client_id(client_id) is None


# Symbol rules


def test_rules_take_the_stricter_lot_filter() -> None:
    assert RULES.max_qty == D(120)  # MARKET_LOT_SIZE
    assert RULES.step_size == D("0.001")
    assert RULES.margin_asset == "USDT"


def test_rounding_to_filters() -> None:
    assert RULES.floor_quantity(D("0.08333")) == D("0.083")
    assert RULES.floor_price(D("59397.59")) == D("59397.5")
    assert RULES.ceil_price(D("59397.51")) == D("59397.6")
    assert RULES.ceil_price(D("59397.6")) == D("59397.6")


@pytest.mark.parametrize(
    ("value", "text"),
    [(D("65.50"), "65.5"), (D("1E+1"), "10"), (D("0.0010"), "0.001"), (D("0.00"), "0")],
)
def test_decimal_param_never_uses_exponents(value: Decimal, text: str) -> None:
    assert decimal_param(value) == text


# Sizing (D-22, D-25)


def test_risk_amount_is_a_percentage_of_the_base() -> None:
    assert risk_amount(D(5000), D(1)) == D(50)


def test_long_entry_is_sized_from_the_stop_distance() -> None:
    p = plan()
    assert p.quantity == D("0.083")  # 50 / 600 = 0.0833, rounded down
    assert p.stop_price == D(59400)
    assert p.risk == D("49.8")
    assert p.trimmed_by is None
    assert D(48000) < p.liquidation_price < D(48400)


def test_short_entry_mirrors_the_long() -> None:
    p = plan(side=Side.SHORT, stop_price=D(60600))
    assert p.quantity == D("0.083")
    assert D(71500) < p.liquidation_price < D(72000)


def test_stop_is_rounded_toward_the_entry() -> None:
    assert plan(stop_price=D("59400.03")).stop_price == D("59400.1")
    assert plan(side=Side.SHORT, stop_price=D("60600.07")).stop_price == D(60600)


@pytest.mark.parametrize(("side", "stop"), [(Side.LONG, D(60100)), (Side.SHORT, D(59900))])
def test_stop_on_the_wrong_side_is_rejected(side: Side, stop: Decimal) -> None:
    with pytest.raises(EntryRejected, match="losing side"):
        plan(side=side, stop_price=stop)


def test_position_cap_trims_a_tight_stop() -> None:
    p = plan(stop_price=D(59850))  # 0.25% away -> 4x the base before trimming
    assert p.trimmed_by == "position cap"
    assert p.quantity == D("0.166")  # 10000 / 60000, rounded down
    assert p.risk < D(50)


def test_available_margin_trims() -> None:
    p = plan(available_balance=D(800))  # 800 x 5 = 4000 notional
    assert p.trimmed_by == "available margin"
    assert p.quantity == D("0.066")


def test_too_small_after_trimming_is_rejected() -> None:
    with pytest.raises(EntryRejected, match="below the minimum"):
        plan(risk_amount=D("0.5"))


def test_stop_beyond_half_the_liquidation_distance_is_rejected() -> None:
    # 10x: liquidation about 9.6% away; a 6% stop is past half of that.
    with pytest.raises(EntryRejected, match="liquidation"):
        plan(leverage=10, stop_price=D(56400))


def test_zero_risk_is_rejected() -> None:
    with pytest.raises(EntryRejected, match="risk"):
        plan(risk_amount=D(0))


def test_max_notional_at_leverage_uses_brackets_that_allow_it() -> None:
    assert max_notional_at(BRACKETS, 5) == D(3000000)
    assert max_notional_at(BRACKETS, 110) == D(50000)
    with pytest.raises(EntryRejected):
        max_notional_at(BRACKETS, 126)


def test_liquidation_uses_the_bracket_of_the_notional() -> None:
    # Notional 120000 falls in bracket 2 (mmr 0.005, cum 50).
    lp = isolated_liquidation_price(Side.LONG, D(60000), D(2), 5, BRACKETS)
    assert lp == (D(120000) - D(24000) - D(50)) / (D(2) * (1 - D("0.005")))


# Emergency stops (D-26)


def test_emergency_stop_loses_one_risk_amount() -> None:
    stop = emergency_stop_price(Side.LONG, D(60000), D("0.083"), D(50), RULES)
    assert stop == D("59397.6")  # 60000 - 50 / 0.083 = 59397.59, rounded toward the entry
    assert (D(60000) - stop) * D("0.083") <= D(50)


def test_emergency_stop_for_a_short_uses_the_absolute_size() -> None:
    stop = emergency_stop_price(Side.SHORT, D(60000), D("-0.083"), D(50), RULES)
    assert stop == D("60602.4")


def test_emergency_stop_is_tighter_for_an_oversized_position() -> None:
    normal = emergency_stop_price(Side.LONG, D(60000), D("0.083"), D(50), RULES)
    oversized = emergency_stop_price(Side.LONG, D(60000), D("0.830"), D(50), RULES)
    assert normal < oversized < D(60000)
