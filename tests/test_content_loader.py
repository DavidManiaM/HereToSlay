"""Phase 1: YAML in, validated immutable ContentRegistry out."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from here_to_slay.content import ContentError, load_pack
from here_to_slay.content.loader import _deep_merge, _set_dotted
from here_to_slay.content.schema import HeroDef, MonsterDef


@pytest.fixture(scope="module")
def good(fixtures: Path):
    return load_pack(fixtures / "good")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_loads_cards_and_dependency_rules(good) -> None:
    assert set(good.cards) == {
        "good.hero.pickpocket",
        "good.monster.slime",
        "good.modifier.plus_two",
        "good.leader.song",
    }
    # rules came from the required pack, not from this one
    assert good.rules.id == "fixture"
    assert good.pack_ids == ("fixture_rules", "good")


def test_dependencies_load_before_dependents(good) -> None:
    assert good.pack_ids.index("fixture_rules") < good.pack_ids.index("good")


def test_cards_parse_into_their_kind(good) -> None:
    hero = good["good.hero.pickpocket"]
    assert isinstance(hero, HeroDef)
    assert hero.card_class == "thief"
    assert hero.ability is not None
    assert hero.ability.roll is not None
    assert hero.ability.roll.spec == (2, 6, 0)
    assert hero.ability.roll.range == (2, 12)

    monster = good["good.monster.slime"]
    assert isinstance(monster, MonsterDef)
    assert monster.requirement is not None
    assert monster.requirement.op == "party_has_class"
    assert monster.requirement.param("class") == "fighter"


def test_registry_is_immutable(good) -> None:
    with pytest.raises(TypeError):
        good.cards["good.hero.pickpocket"] = None  # type: ignore[index]
    with pytest.raises(ValidationError):
        good.rules.classes = []  # type: ignore[misc]


def test_deck_composition_counts_copies(good) -> None:
    assert good.deck_composition() == {
        "main_deck": 2 + 4,  # pickpocket x2, +2 modifier x4
        "monster_deck": 1,
        "leader_pool": 2,
    }


def test_content_hash_is_stable_and_content_sensitive(good, fixtures: Path) -> None:
    assert good.content_hash == load_pack(fixtures / "good").content_hash
    assert good.content_hash != load_pack(fixtures / "rules_only").content_hash


def test_pack_yaml_file_may_be_given_directly(fixtures: Path) -> None:
    assert load_pack(fixtures / "good" / "pack.yaml").pack_ids == ("fixture_rules", "good")


def test_missing_path_is_reported_not_raised_raw(tmp_path: Path) -> None:
    with pytest.raises(ContentError) as exc:
        load_pack(tmp_path / "nope")
    assert "does not exist" in str(exc.value)


def test_missing_dependency_names_the_pack(tmp_path: Path) -> None:
    (tmp_path / "orphan").mkdir()
    (tmp_path / "orphan" / "pack.yaml").write_text(
        "id: orphan\nrequires: [nonexistent]\n", encoding="utf-8"
    )
    with pytest.raises(ContentError) as exc:
        load_pack(tmp_path / "orphan")
    assert "required pack 'nonexistent' not found" in str(exc.value)


# ---------------------------------------------------------------------------
# Structural failures are path-qualified
# ---------------------------------------------------------------------------


def test_duplicate_id_is_rejected_with_both_locations(fixtures: Path) -> None:
    with pytest.raises(ContentError) as exc:
        load_pack(fixtures / "broken_duplicate_id")
    issue = _only_matching(exc.value, "duplicate card id")
    assert issue.path.endswith("cards.yaml[1]")
    assert "broken_duplicate_id.hero.twin" in issue.message
    assert "cards.yaml[0]" in issue.message


def test_schema_errors_point_at_the_offending_key(fixtures: Path) -> None:
    with pytest.raises(ContentError) as exc:
        load_pack(fixtures / "broken_schema")
    paths = {issue.path for issue in exc.value.errors}
    assert "tests/fixtures/broken_schema/cards.yaml[0].card_class" in paths
    assert "tests/fixtures/broken_schema/cards.yaml[0].power" in paths


def test_invalid_yaml_reports_a_line_number(tmp_path: Path) -> None:
    pack = tmp_path / "bad_yaml"
    (pack / "cards").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        "id: bad_yaml\nprovides:\n  rules: rules.yaml\n  cards: ['cards/*.yaml']\n",
        encoding="utf-8",
    )
    (pack / "rules.yaml").write_text("id: bad_yaml\n", encoding="utf-8")
    (pack / "cards" / "c.yaml").write_text("- id: x\n  name: 'unterminated\n", encoding="utf-8")

    with pytest.raises(ContentError) as exc:
        load_pack(pack)
    assert any("invalid YAML" in issue.message for issue in exc.value.errors)


# ---------------------------------------------------------------------------
# Merging and patching — how a variant ships as a diff
# ---------------------------------------------------------------------------


def test_deep_merge_merges_dicts_and_replaces_scalars() -> None:
    base = {"turn": {"action_points_per_turn": 3, "hand_limit": None}}
    overlay = {"turn": {"action_points_per_turn": 4}}
    assert _deep_merge(base, overlay) == {"turn": {"action_points_per_turn": 4, "hand_limit": None}}


def test_deep_merge_merges_id_lists_by_id() -> None:
    base = {"actions": [{"id": "draw", "cost": {"action_points": 1}}, {"id": "gone"}]}
    overlay = {
        "actions": [
            {"id": "draw", "cost": {"action_points": 2}},
            {"id": "gone", "remove": True},
            {"id": "new"},
        ]
    }
    merged = _deep_merge(base, overlay)
    assert merged["actions"] == [
        {"id": "draw", "cost": {"action_points": 2}},
        {"id": "new"},
    ]


def test_set_dotted_walks_lists_and_dicts() -> None:
    target = {"roll": {"outcomes": [{"min": 8}, {"max": 3}]}}
    assert _set_dotted(target, "roll.outcomes.0.min", 11) is None
    assert target["roll"]["outcomes"][0]["min"] == 11
    assert _set_dotted(target, "roll.nope.0.min", 1) is not None


def test_a_variant_patches_the_base_pack(tmp_path: Path, fixtures: Path) -> None:
    variant = tmp_path / "grimdark"
    variant.mkdir()
    (variant / "pack.yaml").write_text(
        """
id: grimdark
requires: [good]
patches:
  - target: good.monster.slime
    set: {roll.outcomes.0.min: 11}
  - target: good.modifier.plus_two
    remove: true
  - target: rules
    set: {turn.action_points_per_turn: 4}
""",
        encoding="utf-8",
    )

    registry = load_pack(variant, search_paths=[fixtures])
    assert registry.rules.turn.action_points_per_turn == 4
    assert "good.modifier.plus_two" not in registry
    monster = registry["good.monster.slime"]
    assert monster.roll.outcomes[0].min == 11
    # the base pack on disk is untouched
    assert load_pack(fixtures / "good")["good.monster.slime"].roll.outcomes[0].min == 8


def test_patch_at_a_missing_target_is_an_error(tmp_path: Path, fixtures: Path) -> None:
    variant = tmp_path / "typo"
    variant.mkdir()
    (variant / "pack.yaml").write_text(
        "id: typo\nrequires: [good]\npatches:\n  - target: good.monster.slimee\n    remove: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as exc:
        load_pack(variant, search_paths=[fixtures])
    assert "no such card 'good.monster.slimee'" in str(exc.value)


def test_a_later_pack_may_not_silently_redefine_an_id(tmp_path: Path, fixtures: Path) -> None:
    variant = tmp_path / "clash"
    variant.mkdir()
    (variant / "pack.yaml").write_text(
        "id: clash\nrequires: [good]\nprovides:\n  cards: ['cards.yaml']\n", encoding="utf-8"
    )
    (variant / "cards.yaml").write_text(
        "- id: good.monster.slime\n  kind: monster\n  name: 'Impostor'\n"
        "  roll: {dice: '2d6', outcomes: [{effect: {op: noop}}]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ContentError) as exc:
        load_pack(variant, search_paths=[fixtures])
    assert "duplicate card id" in str(exc.value)
    assert "patches" in str(exc.value)


# ---------------------------------------------------------------------------


def _only_matching(error: ContentError, needle: str):
    matches = [issue for issue in error.issues if needle in issue.message]
    assert matches, f"no issue mentioning {needle!r} in:\n{error}"
    return matches[0]
