"""`hts validate` must be CI-able: non-zero exit and a path-qualified table."""

from __future__ import annotations

from pathlib import Path

import pytest

from here_to_slay.cli import EXIT_CONTENT_ERROR, EXIT_OK, EXIT_USAGE, main


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the report width so assertions do not depend on the shell.

    ``rich`` folds a long cell to fit the terminal, which splits a file path
    across two lines and hides it from a substring check. Without this a
    developer in a narrow window — or in one advertising ``TERM=dumb``, where
    rich assumes 80 columns and ignores ``COLUMNS`` — sees these tests fail on
    code they never touched.
    """
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("COLUMNS", "200")


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


def test_strict_promotes_warnings(fixtures: Path) -> None:
    """A pack that only *warns* passes normally and fails under --strict.

    The rules-only fixture warns about an undealable deck. It replaced
    ``data/base`` here at Phase 6: base now ships a full deck and validates
    clean, so it no longer produces a warning to promote.
    """
    pack = str(fixtures / "rules_only")
    assert main(["validate", pack]) == EXIT_OK
    assert main(["validate", pack, "--strict"]) == EXIT_CONTENT_ERROR


def test_the_shipping_pack_validates_clean(project_root: Path) -> None:
    """The base game is warning-free, which is the Phase 6 acceptance gate."""
    base = str(project_root / "data" / "base")
    assert main(["validate", base]) == EXIT_OK
    assert main(["validate", base, "--strict"]) == EXIT_OK


def test_quiet_prints_only_the_summary(fixtures: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["validate", str(fixtures / "broken_unknown_op"), "--quiet"])
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "unknown effect op" not in out


# ---------------------------------------------------------------------------
# Phase 10 — the modding commands
# ---------------------------------------------------------------------------


def test_validate_accepts_a_pack_whose_ops_come_from_a_plugin(project_root: Path) -> None:
    """Without plugin loading this exits 1 on five 'unknown op' errors — which
    is exactly what `validate` did before Phase 10."""
    variant = str(project_root / "data" / "variants" / "overclock")
    assert main(["validate", variant]) == EXIT_OK
    assert main(["validate", variant, "--strict"]) == EXIT_OK


def test_a_required_pack_one_directory_up_is_found_without_a_search_path(
    project_root: Path,
) -> None:
    """`data/variants/overclock` requires `base`, which lives in `data/`. A
    modder's first run must not need to learn --search-path."""
    assert main(["validate", str(project_root / "data" / "variants" / "overclock")]) == EXIT_OK


def test_new_pack_writes_something_that_validates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new-pack", "scaffolded", "--dir", str(tmp_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Created" in out
    assert (tmp_path / "scaffolded" / "pack.yaml").is_file()
    assert main(["validate", str(tmp_path / "scaffolded"), "--search-path", "data"]) == EXIT_OK


def test_new_pack_refuses_a_bad_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new-pack", "Bad Id", "--dir", str(tmp_path)]) == EXIT_USAGE
    assert "lower_snake_case" in capsys.readouterr().out


def test_diff_pack_lists_the_new_ops_and_the_new_zone(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    variant = str(project_root / "data" / "variants" / "overclock")
    assert main(["diff-pack", str(project_root / "data" / "base"), variant]) == EXIT_OK
    out = capsys.readouterr().out
    assert "upload_card" in out
    assert "cache_burn" in out
    assert "zones[cache].scope" in out
    assert "rule change(s)" in out


def test_diff_pack_of_a_pack_against_itself_says_so(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = str(project_root / "data" / "base")
    assert main(["diff-pack", base, base]) == EXIT_OK
    assert "No differences" in capsys.readouterr().out
