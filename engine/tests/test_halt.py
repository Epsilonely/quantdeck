import logging
from pathlib import Path

import pytest

from quantdeck_engine.cli import lift_halt
from quantdeck_engine.trading.halt import HaltRecord, HaltStore

RECORD = HaltRecord(reason="today's loss reached the limit", halted_at="2026-10-16T03:00:00+00:00")


def test_save_then_load(tmp_path: Path) -> None:
    store = HaltStore(tmp_path / "state" / "halt.json")
    store.save(RECORD)
    assert store.load() == RECORD
    assert [p.name for p in store.path.parent.iterdir()] == ["halt.json"]  # no temp file left


def test_no_file_means_not_halted(tmp_path: Path) -> None:
    assert HaltStore(tmp_path / "halt.json").load() is None


@pytest.mark.parametrize("content", ["{not json", "[]", '{"reason": "x"}', ""])
def test_unreadable_file_counts_as_halted(
    tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "halt.json"
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        record = HaltStore(path).load()
    assert record is not None and "unreadable" in record.reason
    assert record.halted_at == ""
    assert "treating it as halted" in caplog.text


def test_save_failure_raises(tmp_path: Path) -> None:
    (tmp_path / "state").write_text("a file where the directory should be")
    with pytest.raises(OSError):
        HaltStore(tmp_path / "state" / "halt.json").save(RECORD)


def test_clear(tmp_path: Path) -> None:
    store = HaltStore(tmp_path / "halt.json")
    store.save(RECORD)
    store.clear()
    assert store.load() is None
    store.clear()  # nothing to delete is fine


def test_lift_halt_deletes_the_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    store = HaltStore(tmp_path / "halt.json")
    store.save(RECORD)
    with caplog.at_level(logging.WARNING):
        lift_halt(store)
    assert store.load() is None
    assert "Halt lifted" in capsys.readouterr().out
    assert "lifted by the user" in caplog.text and RECORD.reason in caplog.text


def test_lift_halt_when_not_halted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lift_halt(HaltStore(tmp_path / "halt.json"))
    assert "Not halted" in capsys.readouterr().out
