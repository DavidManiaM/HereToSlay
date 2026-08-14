"""`hts validate` must be CI-able: non-zero exit and a path-qualified table."""

from __future__ import annotations

from pathlib import Path

import pytest

from here_to_slay.cli import EXIT_CONTENT_ERROR, EXIT_OK, EXIT_USAGE, main


def test_no_command_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_USAGE
    assert "usage: hts" in capsys.readouterr().out


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_validate_good_pack(fixtures: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(fixtures / "good")]) == EXIT_OK
    out = capsys.readouterr().out
    assert "OK" in out
    assert "4 card definitions" in out


def test_validate_reports_the_content_hash(
    fixtures: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["validate", str(fixtures / "good")])
    assert "content hash" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("pack", "needle"),
    [
        ("broken_unknown_op", "unknown effect op"),
        ("broken_unbound_ref", "not bound"),
        ("broken_bad_class", "unknown class"),
        ("broken_band_gap", "do not cover"),
        ("broken_duplicate_id", "duplicate card id"),
        ("broken_schema", "card_class"),
    ],
)
def test_validate_fails_on_each_broken_fixture(
    pack: str, needle: str, fixtures: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["validate", str(fixtures / pack)]) == EXIT_CONTENT_ERROR
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert needle in out, out
    assert f"{pack}/cards.yaml" in out, out


def test_strict_promotes_warnings(project_root: Path) -> None:
    base = str(project_root / "data" / "base")
    assert main(["validate", base]) == EXIT_OK
    assert main(["validate", base, "--strict"]) == EXIT_CONTENT_ERROR


def test_quiet_prints_only_the_summary(fixtures: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["validate", str(fixtures / "broken_unknown_op"), "--quiet"])
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "unknown effect op" not in out
