# Strategy Research

Decisions about the backtest and the strategy search, with the evidence behind them: how the backtest sizes and fills trades, and which strategies were tested and why each was kept or dropped. The other design decisions are in `decisions.md`. Both files share one numbering and the same rules: a new entry takes the next number after the highest in either file and goes at the bottom, and a reversed decision is marked `Superseded by D-XX`, never deleted.

## D-37 The backtest reuses the live sizing and risk rules

- **Decision:**
  - The backtest sizes entries with the live code (D-22, D-25): the risk amount from the day's base balance, `stepSize` rounding, `MIN_NOTIONAL`, the notional caps, and the liquidation-distance check. It also applies the daily loss limit and the entry gate (D-23, D-24).
  - When the simulated day's loss reaches the limit, the backtest closes the position and takes no entries for the rest of that UTC day, then resumes the next UTC day as if the user had lifted the halt (D-27). The report counts how often this happened.
- **Why:** The results then include what the live engine would really skip or trim, such as entries below `MIN_NOTIONAL` or entries capped by the room left under the limit. How often the limit fires is one of the criteria for choosing a strategy (D-39). Live, a halt waits for the user; a backtest has no user, so it lifts at the next day boundary, the earliest a lift changes anything (D-27).
- **Note:** The rest of the fill model is D-40.
- **Date:** 2026-10-18

## D-38 Backtest candles come from Binance's mainnet archive and REST, kept as the downloaded zip files

- **Decision:**
  - Backtests run on mainnet data: monthly zips from data.binance.vision (`futures/um`), daily zips for the current month, and REST `GET /fapi/v1/klines` for the days not yet archived. Every zip is checked against its `.CHECKSUM`, and loaded candles are checked for gaps and duplicates. Mark price klines and funding rates come from the same archive when the fill model needs them.
  - To check a forward test (D-36), the backtest replays the testnet candles the engine actually saw, fetched from testnet REST, not mainnet data.
  - The zip files are kept as downloaded in `engine/data/` (gitignored) and read directly, with no conversion step. Parquet (D-04) waits until the data outgrows this, such as years of 1m candles or trade data.
- **Why:** Testnet prices and volume differ from mainnet's, and there is no testnet archive (`binanceNotes.md`), so mainnet data is the closest to the market a strategy would eventually trade. The archive is free, needs no key, and comes with checksums; REST fills in the days not archived yet. Keeping the original files means they can always be re-verified against Binance's checksums. Seven years of BTCUSDT 1h candles is about 60,000 rows, small enough to read from the zips on every run, so Parquet would add a large dependency (pyarrow or similar) and a conversion step for no gain yet.
- **Alternatives:** Parquet from the start (D-04); REST only (slow paging, rate limits, no checksums); testnet data.
- **Note (2026-10-29, GitHub issue #40):** Reading and checking a coin's 15m history took 2.5–8.5 s, and a grid run as one backtest per setting repeated it for every setting. `--param` values separated by commas (`entry_period=20,55`) now run every combination over one read, a line each; a combination the strategy refuses is left out with a warning. A run over BTCUSDT's whole history still reads from REST the days its mark price archive lacks (`binanceNotes.md`).
- **Date:** 2026-10-18

## D-39 The first strategy is picked by backtest

- **Decision:** The candidates are a Donchian channel breakout (trend following) and a Bollinger Band mean reversion, both on closed candles (D-36), tested on BTCUSDT first (D-15). Both run through the backtest with costs, and the first strategy is the one that does better on: expectancy per trade after fees, slippage, and funding; maximum drawdown and longest losing streak; and how often the daily loss limit would have fired (D-37). If neither holds up, the candidates change; nothing goes to the forward test on a guess. The chosen strategy is then run on two or three other major coins (ETHUSDT and the like) with the BTCUSDT parameters unchanged; parameters are not tuned per coin.
- **Why:** Which strategy is profitable can't be known before measuring it. Win rate alone doesn't decide it: a trend follower loses often and wins big, a mean reverter wins often and loses big, and either can end up ahead or behind after costs. The two cover both styles, have few parameters, and derive their stop price from their own rules (D-22). The daily limit is a small number of stop-outs (three on testnet, D-24), so long losing streaks mean halts, which is why they are a criterion. A strategy that works only on the coin it was chosen on has likely been fitted to that coin's history; tuning parameters per coin would make that more likely, not less. BTCUSDT comes first for its liquidity (backtest fills closest to real ones) and its archive back to 2020. Trading several coins at once is a separate step (D-15, D-24 note of 2026-10-19).
- **Not a candidate for now:** A volatility breakout, which enters inside a forming candle and exits on time; it doesn't fit D-36.
- **Date:** 2026-10-18
- **Note (2026-10-19):** Both first candidates lost on BTCUSDT 1h after costs, so the candidates changed as this entry says; the choice is D-41.

## D-40 Backtest fill model: next open with slippage, mark-price stops, taker fees, archived funding

- **Decision:**
  - A strategy's entry or exit fills at the next candle's open, moved against the trade by a market slippage (default 0.02%). Entries are sized at that candle's mark price open.
  - Stops trigger when a candle's mark price range reaches them (D-18). They fill at the stop, or at the candle's last price open if the mark price opened past the stop, moved against the trade by a stop slippage (default 0.05%). A fill past the estimated liquidation price is cut at that price.
  - Every fill pays the taker fee (default 0.05%, the regular tier).
  - Funding from the archive settles while a position is open: quantity × mark price × rate. A funding at a candle's open time comes before that candle's fills.
  - Symbol rules and leverage brackets are the configured network's current values (testnet by default); risk settings come from `engine/config.toml` (D-37). The starting balance defaults to 5000 USDT, the period to 2020-01-01 through yesterday, and the report breaks results down by year.
  - All costs are command-line options.
- **Why:** The live engine sends its market order right after a candle closes (D-17, D-36), so the next open is where it fills, and funding at that instant settles first. Triggering on the mark price matches the live stop; mark price candles are in the archive (D-38). Slippage and fees are set on the pessimistic side of a small BTCUSDT order, since an edge that only survives optimistic costs isn't one. The testnet rules and brackets match the forward test's conditions.
- **Limitations:** With hourly candles, where the last price traded when a mark price stop fired is unknown, so the stop price stands in for it. Candles longer than the funding interval settle a funding inside a candle at its open. Current rules and brackets are applied to all of history.
- **Note (2026-10-19):** Limit entries have their own fill model, with the maker fee (D-45). Everything above still applies to market entries, exits, and stops.
- **Date:** 2026-10-19

## D-41 First strategy: Donchian breakout 55/20 on BTCUSDT 4h, at 2× leverage on testnet

- **Decision:** The first strategy for the testnet forward test is the Donchian breakout with entry period 55 and exit period 20 on 4h candles, trading BTCUSDT. Testnet leverage goes from 5× to 2× (D-25). The Bollinger mean reversion is dropped.
- **How it was chosen (D-39), all with D-40 costs and the testnet risk values:**
  - On BTCUSDT 1h with default parameters, both first candidates lost: Donchian 20/10 −27.0% (−0.010 R per trade), Bollinger −99.5% (−0.321 R). Donchian was ahead before costs; Bollinger lost even before them.
  - A grid fixed in advance, Donchian {1h, 4h} × {20/10, 55/20}, was chosen on 2020–2023 and checked on 2024-01-01 to 2026-10-17, at the 5× leverage of the time. 1h was near zero or negative in both periods. On 2020–2023, 4h 55/20 led 4h 20/10 on expectancy (+0.455 vs +0.442 R), drawdown (10.4% vs 11.1%), and losing streak (5 vs 9). On the holdout it stayed positive but weaker: +9.2%, +0.141 R over 68 trades (t ≈ 0.8). At 5× the liquidation-distance check (D-25) skipped 89 and 17 of its entry signals in the two periods. At the 2× it trades, rerun on 2026-10-21 with no skips, BTCUSDT 55/20 gives +0.325 R over 96 trades (+33.8%) on 2020–2023 and +0.068 R over 74 trades (+4.5%) on the holdout; that is the expectancy to hold the forward test against.
  - The same parameters on other coins, full history through 2026-10-17, at 2×: ETHUSDT +0.080 R (179 trades), XRPUSDT +0.201 R (159), SOLUSDT +0.289 R (151); BTCUSDT +0.199 R (172). Max drawdowns 8–14%. The daily limit never fired in any run.
  - At 5×, the D-25 check skipped more entry signals than were traded on the altcoins (ETH 326, XRP 240, SOL 509) and 106 on BTC; a breakout repeats its signal each candle while flat, so these count signals, not distinct trades. The 20-candle exit channel is often more than the ~10% from entry that 5× allows. At 2× BTC skipped none, with similar results, so the 5× results came from a filtered subset of the strategy's trades.
- **Why:** It was positive in the selection period, the holdout, and on three other coins with unchanged parameters, so the result isn't fitted to one coin's history. The forward test on testnet costs nothing, and its job is to show the live engine trades as the backtest does (D-36). Lower leverage doesn't change the planned loss per trade (D-25); it moves the liquidation price to about 50% away and ties up more margin, which is fine with one symbol.
- **Limits of the evidence:** Each result alone is not statistically clear (t between about 0.8 and 2), coins move together so the four don't count as four independent tests, and the holdout expectancy was a third of the selection period's at 5× and a fifth at 2×. That is enough for a forward test, not for mainnet; go-live criteria are still open. Funding cost more than fees in most 4h runs, since positions are held for days.
- **Date:** 2026-10-19
- **Note (2026-10-21), robustness grid:** fixed before the runs, 4h with entry {20, 30, 40, 55, 70, 85, 100} × exit {10, 15, 20, 30, 40} (exit ≤ entry, 32 settings) on BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT at 2×, with D-40 costs (SOLUSDT and XRPUSDT at 0.04% slippage and 0.10% on stops). On 2020–2023 every setting was positive on every coin (BTCUSDT +0.23 to +0.64 R). On 2024 to 2026-10-20 (seen before), 27, 32, 30, and 32 of the 32 were positive, but smaller: around 55/20 on BTCUSDT +0.01 to +0.08 R. So 55/20 sits in a broad positive region rather than on a lucky peak, and the parameters stay. The longer exit (40) did better on BTCUSDT and ETHUSDT in the seen holdout, which is no reason to switch. Win rates were 27–53%; BTCUSDT 55/20 on 2020–2023 won 37.5% of trades, averaging +1.82 R on wins and −0.57 R on losses, since most exits come on the 20-candle channel before the stop. The period held strong trends (2020–21 rally, 2022 crash), which favors any trend follower.

## D-44 15m candidates: the move fade is dropped; the daily-open breakout passed its holdout only nominally and stays backtest-only

- **Decision:** Of the two 15m candidates (GitHub issue #15), the fade of large 1h/2h moves is dropped. The daily-open breakout, long only with k = 0.8, passed the checks fixed before the runs, but it isn't forward-tested: its holdout edge can't be told from zero. The 4h Donchian (D-41) stays the forward test's strategy. Both new strategies stay in the registry for backtests, as the Bollinger reversion does.
- **The strategies (D-36 interface):**
  - Daily-open breakout: O is the 00:00 UTC open and R the previous UTC day's high minus low. The day's first candle to close at or beyond O ± k·R enters that way, with the stop at O, and the position closes on the day's last candle. A day takes at most one entry.
  - Move fade: 1h or 2h bars built from the 15m candles on UTC boundaries. A bar return of at least k·σ against the previous 120 bars' returns enters the other way, with a stop that allows the same move again, and the position closes when the next bar closes.
  - Both exit on clock time read from the candle, never on time since entry, so they fit D-36.
- **How they were tested** (D-40 costs, with SOLUSDT and XRPUSDT at 0.04% slippage and 0.10% on stops; 2× leverage; testnet risk values; the grid and pass rules were fixed in the issue before any run):
  - **Baselines,** BTCUSDT 15m, 2020–2023, after costs (before costs in brackets):
    - Donchian 20/10: −0.153 R per trade (+0.007 R)
    - Donchian 55/20: −0.081 R (+0.014 R)
    - Bollinger: −1.008 R (+0.023 R)

    A 15m decision with a tight stop can't carry about 0.1–0.16 R of costs per trade.
  - **Selection,** 2020–2023 (SOLUSDT from 2020-10) on BTC, ETH, SOL, and XRP, with 7 settings. A setting passed with positive expectancy on BTC and on two of the other three coins, and at least 30 trades on each.
    - The move fade lost on every coin in all three settings, −0.03 to −0.11 R.
    - The daily breakout passed in three of its four settings. Long only with k = 0.8 had the best mean, +0.103 R: BTC +0.143, ETH +0.175, SOL +0.068, XRP +0.025, with 239–300 trades each.
    - Much of that came from 2020. On BTC it made +34% that year and +2–6% in each later year.
  - **Holdout,** 2024-01-01 to 2026-10-18, long only with k = 0.8:
    - BTC +0.011 R (228 trades, t ≈ 0.2), ETH +0.050 R (t ≈ 0.7), SOL +0.023 R (t ≈ 0.4), XRP −0.007 R.
    - That is positive on three of four coins, the rule for passing, but every t is under 0.7, and 2025 lost on BTC, ETH, and SOL.
    - Stops were a median 2–3% from entry, so fees took about 0.04–0.06 R per trade. Max drawdowns were 11–17%, and the daily limit never fired.
- **Why:** The holdout expectancy fell to about a tenth of the selection period's, and the selection result leaned on one bull year in a long-only setting. Passing a rule set before the run is reason to keep the strategy, not to put it on the account in place of the 4h strategy, whose holdout was clearer (+0.141 R over 68 trades at the 5× of the time, +0.068 R over 74 at 2×; D-41). One strategy traded per account then (D-43), so forward-testing it would have meant pausing the 4h test.
- **Revisit:** Once per-coin strategies exist (they do since 2026-10-20, D-47), or after more out-of-sample data. Maker (limit) entries would cut its costs, but they need a fill model first (D-17, D-40).
- **Note (2026-10-19):** The fill model exists now (D-45). The two limit-entry families tested on it failed (D-46); this strategy wasn't among them.
- **Date:** 2026-10-19
- **Note (2026-10-21), Donchian 15m grid:** at the user's request, fixed before the runs: entry {20, 55, 110, 220, 440, 880} × exit {10, 20, 40, 80, 160, 320} (exit ≤ entry, 26 settings), the same coins, costs, and 2× as the 4h grid in D-41's note. On 2020–2023, 15, 17, 12, and 13 of the 26 settings were positive on BTCUSDT, ETHUSDT, SOLUSDT, and XRPUSDT, so the grid fails the rule (a majority on BTCUSDT and two other coins). The split is by time span, not by coin: channels of hours lose on every coin (BTCUSDT 20/10: 3,523 trades, 25.7% won, −0.153 R, −99.2%, the daily limit hit on 34 days), and only channels of several days are positive. 880/320, the same span as 4h 55/20, matched the 4h strategy without beating it consistently (BTCUSDT +0.297 against +0.325 R on 2020–2023, +0.016 against +0.068 R on 2024 onward) with 20–35% more trades. The edge is in multi-day trends; deciding every 15 minutes adds nothing, so the 4h strategy stays. The 2024+ 15m data had been seen before (above), so this counts as one more test on it.

## D-45 Limit entries in the backtest: post-only for one candle, filled only when traded through

- **Decision:**
  - **Interface (extends D-36):** an entry may carry a limit price. The order is placed at the next candle's open, as live would send it right after the close, and cancelled at that candle's close if it hasn't filled; the strategy then decides again. An entry without a limit price stays a market order. Exits stay market orders and stops stay `STOP_MARKET` `closePosition` orders (safety principles 5 and 6). Each strategy declares whether it uses limit entries.
  - **Fill model (extends D-40):**
    - The limit is rounded to the tick away from the market (down for a long, up for a short), and the entry is sized at it with the live sizing, gate, and caps (D-22, D-24, D-25, D-37).
    - A buy limit at or above the next open, or a sell limit at or below it, is rejected, as Binance rejects a post-only (`GTX`) order that would trade at once (`binanceNotes.md`).
    - Otherwise it fills only if the last price trades through it by at least one tick within that candle: a buy when the low reaches the limit minus a tick, a sell when the high reaches the limit plus a tick. A touch doesn't fill.
    - A fill is at the limit, with the maker fee (default 0.02%, the regular tier, `--maker-fee`) and no slippage.
    - If the fill candle's mark price range also reaches the stop, the stop fires in that candle after the fill: at the stop price with the stop slippage, even if the mark price opened past it.
    - The report counts limit entries placed, filled, rejected as crossing, and left unfilled.
  - **Live:** the engine can't place limit entries yet. Loading `config.toml` (D-28) refuses a `[strategy]` that uses them, so `run` doesn't start. If a strategy returns one anyway, the strategy runner stops that symbol's strategy without placing an order (D-43).
- **Why:** The 15m search (D-44) changed only the fee and kept the market entry's time and price. That misses what a limit entry really does: a better price, but also adverse selection (it fills when the price runs through it, against the trade at first) and missed fills (no fill when the price moves away, often the best trades). An edge must survive those before any engine work. Each choice sits on the pessimistic side:
  - One candle only: an order that lived across candles would make the strategy remember it, and a restart could then decide differently (D-36).
  - A trade-through: a touch says nothing about the order's place in the queue, but once the price trades past the limit, every order resting there has filled.
  - The same-candle stop takes the worst order within the candle, since candles can't tell it (D-40 Limitations).
  - Sizing at the limit matches the fill price, so a stop-out still loses about one risk amount (D-22).
  - Refusing live: otherwise a strategy chosen on maker fills would trade at market with taker costs and different fills, which isn't the strategy that was backtested (D-07).
- **Unchanged:** market-entry results are identical. BTCUSDT 4h Donchian 55/20 and 15m Bollinger, 2020–2023, gave the same report and trade list before and after (2026-10-19).
- **Limitations:** No partial fills, queue position, or spread. The fill uses only the last price candle; the mark price only triggers the stop.
- **Later, if a limit-entry strategy survives (out of scope here):** the engine's limit orders (`GTX`, expired by `GTD` or cancelled by the engine at the next close), partial fills, and counting a pending entry's risk at the entry gate (D-24).
- **Alternatives:** Changing only the fee (what D-44 measured); an order that rests until filled or replaced; filling on a touch; 1m candles to order events within a candle.
- **Note (2026-10-19):** No limit-entry strategy survived the first test of this model (D-46), so the engine work above waits.
- **Note (2026-10-20):** Since D-47 the same check refuses such a strategy in `symbols.json` at startup and in a client's `set_symbol`.
- **Date:** 2026-10-19

## D-46 Limit-entry candidates: neither family passed, so no limit-entry strategy goes forward

- **Decision:** Two limit-entry families were tested on the D-45 fill model (GitHub issue #17). The Bollinger reversion with a deeper limit is dropped, and the trend pullback's chosen setting failed its holdout. No limit-entry strategy goes to a forward test, the engine gets no limit orders for now, and the grid isn't widened. Both stay in the registry for backtests.
- **The strategies (D-36 interface, 1h candles, each limit valid for one candle):**
  - Bollinger with a limit offset: the existing signal (a close outside the 20-candle mean ± 2σ), entered with a limit a·σ beyond the close and the stop s·σ beyond the limit. a = 0 is the existing market entry.
  - Trend pullback: 4h bars built from the 1h candles. The trend is the last bar's close against the mean of the last 55 bar closes, and ATR is the mean true range of the last 14 bars. While flat, every candle enters with the trend, with a limit k·ATR beyond its close and the stop m·ATR beyond the limit. The position closes when a bar closes across the mean. k = 0 enters at market.
- **How they were tested** (rules fixed in the issue before any run; D-40 costs with the 0.02% maker fee, SOLUSDT and XRPUSDT at 0.04% slippage and 0.10% on stops; 2× leverage; testnet risk values):
  - **Selection,** 2020–2023 (SOLUSDT from 2020-10) on BTC, ETH, SOL, and XRP: 8 limit settings, a or k ∈ {0.5, 1} × s or m ∈ {2, 3}, each against the market control with the same stop. A setting passed with positive expectancy on BTC and two of the other three coins (at least 30 trades each) and with a higher expectancy than its control on BTC and two of the other three.
  - **Bollinger:** the limit cut its control's loss by a fifth to a third (means −0.077 to −0.103 R against −0.114 and −0.157 R), but every setting lost on every coin (t −1.4 to −4.1). Before costs, BTC was about zero across all six settings (−0.020 to +0.029 R): a limit saves costs, but there was no edge to save them for.
  - **Trend pullback:** all four limit settings passed both rules (means +0.129 to +0.236 R; controls +0.105 and +0.156 R). k = 1, m = 2 had the highest mean, +0.236 R: BTC +0.156, ETH +0.013, SOL +0.517, XRP +0.260 (180–303 trades, t 0.1–1.5). SOL carried the mean, and ETH was flat.
  - **Fills:** with k = 1, 1–2% of the limit orders filled (about 8% at k = 0.5), since a new order went out every hour while flat. None was rejected as crossing: a 1h candle opens next to the previous close.
  - **Holdout,** 2024-01-01 to 2026-10-18, k = 1, m = 2: positive on ETH only (+0.058 R); BTC −0.076, SOL −0.029, XRP −0.022 (176–228 trades, every |t| under 1). The rule needed three of four. Its market control was positive on all four but weak (mean +0.077 R, t 0.1–0.8), so the limit's lead from the selection period reversed.
- **Why:** The rules set before the runs decide it: the Bollinger family failed the selection, and the trend pullback failed its holdout. Deep limit fills in a trend look adversely selected: they catch the dips that keep going and miss the trends that never pull back. A limit changes which trades are taken more than what they cost, so it can't create an edge the signal lacks.
- **By-product, not a candidate:** the trend pullback's market controls were positive on every coin in both periods, weakly in the holdout. They are a 4h trend follower, like the strategy already in the forward test (D-41). They weren't candidates here, and the holdout has now been seen, so a test of them would count as one more on the same history.
- **Revisit:** Limit entries only with a signal that has an edge before costs, or if slippage on a larger account makes market entries costly.
- **Date:** 2026-10-19

## D-52 Turtle-style variants of the 4h Donchian: an ATR stop and an ADX filter pass on 4h only; settable, but not shown to beat the plain breakout

- **Decision:** The strategy `donchian-atr-adx` adds two optional turtle-style parts to the Donchian breakout (D-41): a stop `stop_atr` ATR(20) beyond the entry candle's close in place of the exit channel's opposite extreme, and skipping entries while Wilder's ADX(14) is below `adx_min`. At the user's request (2026-10-22) symbols can be given it, with both parts on as the defaults (1.5 ATR, ADX 20) and either part off at 0. Neither part beat the plain breakout consistently, and the defaults failed their own rule (below), so the BTCUSDT forward test keeps the channel stop unless the user changes it. D-39's rule for putting a strategy on a coin applies as before.
- **How they were tested** (GitHub issue #26; rules fixed in the issue before each run; 55/20, 2×, D-40 costs with SOLUSDT and XRPUSDT at 0.04% slippage and 0.10% on stops, testnet risk values; BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT). A setting passed with positive expectancy, and expectancy and return above the plain breakout's, on BTCUSDT and two of the other three:
  - 4h, 2020–2023 (SOLUSDT from 2020-10): the ATR stops 1.5, 2, and 3 and ADX 20 passed, ADX 25 didn't. The highest mean R of each family, 1.5 ATR and ADX 20, went on.
  - 4h, 2024-01-01 to 2026-10-21: both passed. On BTCUSDT the plain breakout made +0.066 R per trade (+4.3%, drawdown 10.0%), 1.5 ATR +0.123 R (+6.9%, drawdown 30.0%), ADX 20 +0.106 R (+6.7%, 8.4%). ADX 20 lost on SOLUSDT (−0.005 R).
  - Both parts together, added at the user's request with a third rule (a smaller drawdown than 1.5 ATR alone on BTCUSDT and two others), failed: on 2020–2023 it beat the plain breakout only on BTCUSDT and ETHUSDT, its drawdown was smaller on only two coins in each period, and SOLUSDT lost on 2024 onward (−0.096 R, −10.5%).
  - 1h and 1d, as a check of the 4h result (1d over the whole span as one period with at least 20 trades, since each half had too few): 1.5 ATR failed on 1h (a lower return than the plain breakout on BTCUSDT and SOLUSDT from 2024) and passed on 1d; ADX 20 passed on 1h and failed on 1d (SOLUSDT and XRPUSDT negative, ETHUSDT 16 trades). Neither held on both, which the rule set beforehand reads as a 4h advantage that may not carry over.
- **Why the passes are weak:**
  - At a fixed risk per trade (D-22), a tighter stop means a bigger position; per-trade fees showed positions 3–6 times larger. The 1.5 ATR stop's higher R and return came with drawdowns 2–3 times the plain breakout's (BTCUSDT 30% and XRPUSDT 31% on 4h from 2024; 30–67% on 1h) and losing streaks up to 18 on 4h and 25 on 1h. Its return per unit of drawdown beat the plain breakout on only two of four coins in each 4h period, and its t-values were no higher (BTCUSDT from 2024: 0.34 against 0.42).
  - Picking each family's setting by mean R favored the tightest stop, since a tighter stop shrinks 1 R. Return per unit of drawdown would have been the fairer rule.
  - The drawdowns come from many small stop-outs plus open profit given back before the channel exit, not from single trades: the worst trade lost 1.3 R. On XRPUSDT one trade in the November 2024 rally (+126.7 R) was larger than the 1.5 ATR stop's whole 4h result from 2024 (+103 R).
  - ADX 20's gain was small (about +0.04 R per trade on BTCUSDT), every t-value was under 2, and it did worse than the plain breakout on SOLUSDT in both 4h periods.
  - On 1d at 2×, the 20-day extreme is often more than half the liquidation distance away (D-25), so the plain breakout skipped most of its entry signals (SOLUSDT 88, XRPUSDT 48, ETHUSDT 41) and traded only 10–27 times per coin. The 1.5 ATR stop's 1d pass reflects that, not a better stop.
  - Every period had been seen before (D-41), so each run counts as one more test on the same history.
- **Also seen:** the plain 55/20 does best on 4h. On 1h it is near zero on BTCUSDT (+0.005 R on 2020–2023, +0.027 R from 2024), and on 1d at 2× it barely trades, so the original turtle scale doesn't fit this leverage on altcoins.
- **ADX live:** Wilder's smoothing never forgets, so the ADX is recomputed from the last 11 × `adx_period` candles whenever an entry needs it, and a restart gives the same value (D-36); the window's starting values weigh about 0.01%. With the filter on, the warm-up is 154 candles on the default period.
- **Date:** 2026-10-22
- **Note (2026-10-29):** On 15m no ADX setting made the strategy positive (D-57).

## D-57 No ADX setting rescues `donchian-atr-adx` on 15m

- **Decision:** On 15m, `donchian-atr-adx` with a 1.5 ATR(20) stop loses after costs on channels 55/20, 12/10, and 12/5, whatever its `adx_period` and `adx_min`. No ADX setting is recommended for 15m, and the grid isn't widened. ADX on 15m changes how often the strategy trades, not whether it loses.
- **How it was tested** (GitHub issue #37; rules fixed in the issue before each run; 2×, D-40 costs with SOLUSDT and XRPUSDT at 0.04% slippage and 0.10% on stops, testnet risk values; BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT; 2020–2023 to select, 2024-01-01 to 2026-10-28 to check):
  - For each channel, `adx_period` {14, 28, 56, 90} × `adx_min` {15, 20, 25, 30, 40} against the filter off. 90 is the most the engine allows (warm-up 11 × 90 = 990 of the 1000-candle bound), so 4h ADX(14)'s 56-hour span (224 candles) can't be set.
  - The pass rules were #26's: positive expectancy on BTCUSDT and two of the other three with at least 30 trades each, and expectancy and return above the filter-off control on the same.
  - 55/20 is the strategy's default and was run first. 12/10 and 12/5 were added because they are what the user had set on ETHUSDT and XRPUSDT (2026-10-29).
- **Results:**
  - Nothing passed in 2020–2023 on any channel. Every control lost on every coin: 33–100% on 55/20 and at least 97% on the 12-candle channels.
  - On 55/20 the control made about 2,000 trades per coin, 20–24% of them winners, and about 70% of exits came at the stop. BTCUSDT's fees came to about the 5000 USDT starting balance, and the daily limit was hit on 84–128 days per coin.
  - Stricter filters lost less only by trading less; expectancy stayed negative in nearly every cell. The few positive cells were scattered, with t under 0.7. Longer periods did worse, mostly −0.2 to −0.7 R.
  - On the 12-candle channels, ADX 14/20 kept about 90% of the trades and left expectancy the same or worse (12/5 from 2024: −0.382 R against the control's −0.365 R).
  - The settings exactly as set (XRPUSDT 12/5 at 3×, ETHUSDT 12/10 at 5×, both ADX 14/20) lost nearly the whole balance in both periods. From 2024 they hit the daily limit on 153 and 193 days, about 4.5 and 5.7 times a month.
  - From 2024, run for reference only: 55/20's pick, 56/40, traded 4–16 times per coin. 12/5's pick, 14/40, lost on every coin. 12/10's pick, 90/30, met both rules (+0.010 to +0.218 R on three coins, t 0.02–0.59, 44–90 trades), but it had lost on three of four coins in 2020–2023, so its sign flips with the period and it isn't adopted.
- **Lesson for future rules:** a pick that breaks ties by mean return ÷ drawdown favors a setting that hardly trades, since it hardly loses. Any pick by a ratio needs the trade minimum too; the 12-candle runs applied it.
- **For the engine exercises:** any of these settings on an exercise symbol would hit the daily limit several times a month in the backtest, and the kill switch would halt the BTCUSDT forward test with it.
- **Date:** 2026-10-29
