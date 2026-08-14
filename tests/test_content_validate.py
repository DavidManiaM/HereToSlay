"""Phase 1: the semantic pass (docs/card_schemas.md §8).

Every broken fixture must fail with a *path-qualified* message — a modder should
be told which key is wrong, not handed a KeyError twenty minutes into a game.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from here_to_slay.content import ContentError, load_pack, validate_registry
from here_to_slay.content.errors import ContentIssue
from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.schema import CardDef, PackDef, RuleSet

_CARDS: TypeAdapter[CardDef] = TypeAdapter(CardDef)


@pytest.fixture(scope="module")
def rules(request: pytest.FixtureRequest) -> RuleSet:
    fixtures = Path(request.config.rootpath) / "tests" / "fixtures"
    return load_pack(fixtures / "rules_only").rules


def registry_with(rules: RuleSet, *cards: dict[str, Any]) -> ContentRegistry:
    """A registry built in memory, so a test can be one card long."""
    return ContentRegistry(
        rules=rules,
        cards={card["id"]: _CARDS.validate_python(card) for card in cards},
        packs=(PackDef(id="t"),),
        sources={card["id"]: card["id"] for card in cards},
    )


def hero(effect: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    card = {
        "id": "t.hero.probe",
        "kind": "hero",
        "name": "Probe",
        "card_class": "bard",
        "ability": {"activation": "action", "effect": effect},
    }
    return {**card, **overrides}


def errors_matching(issues: list[ContentIssue], needle: str) -> list[ContentIssue]:
    return [i for i in issues if i.is_error and needle in i.message]


# ---------------------------------------------------------------------------
# The good pack
# ---------------------------------------------------------------------------


def test_good_pack_validates_clean(fixtures: Path) -> None:
    registry = load_pack(fixtures / "good")
    assert validate_registry(registry) == []


def test_base_pack_has_no_errors(project_root: Path) -> None:
    registry = load_pack(project_root / "data" / "base")
    assert [i for i in validate_registry(registry) if i.is_error] == []


# ---------------------------------------------------------------------------
# The broken fixtures
# ---------------------------------------------------------------------------


def test_unknown_op_is_reported_with_a_suggestion(fixtures: Path) -> None:
    issues = validate_registry(load_pack(fixtures / "broken_unknown_op"))
    found = errors_matching(issues, "unknown effect op 'drawww'")
    assert found, issues
    assert found[0].path.endswith("cards.yaml[0].ability.effect.op")
    assert found[0].hint == "did you mean 'draw'?"


def test_ref_used_before_it_is_bound(fixtures: Path) -> None:
    issues = validate_registry(load_pack(fixtures / "broken_unbound_ref"))
    found = errors_matching(issues, "'$victim' is not bound here")
    assert found, issues
    assert found[0].path.endswith("cards.yaml[0].ability.effect.steps[0].from")


def test_unknown_card_class(fixtures: Path) -> None:
    issues = validate_registry(load_pack(fixtures / "broken_bad_class"))
    found = errors_matching(issues, "unknown class 'paladin'")
    assert found, issues
    assert found[0].path.endswith("cards.yaml[0].card_class")


def test_bands_must_cover_the_dice_range(fixtures: Path) -> None:
    issues = validate_registry(load_pack(fixtures / "broken_band_gap"))
    found = errors_matching(issues, "do not cover every possible total")
    assert found, issues
    assert found[0].path.endswith("roll.outcomes")
    assert "2, 3, 7" in found[0].message


def test_structurally_broken_fixtures_never_reach_the_semantic_pass(fixtures: Path) -> None:
    for name in ("broken_schema", "broken_duplicate_id"):
        with pytest.raises(ContentError):
            load_pack(fixtures / name)


# ---------------------------------------------------------------------------
# Binding scope
# ---------------------------------------------------------------------------


def test_choose_binds_for_later_steps_only(rules: RuleSet) -> None:
    ok = registry_with(
        rules,
        hero(
            {
                "op": "seq",
                "steps": [
                    {"op": "choose", "bind": "victim", "from": {"selector": "players"}},
                    {"op": "draw", "target": "$victim", "count": 1},
                ],
            }
        ),
    )
    assert errors_matching(validate_registry(ok), "not bound") == []

    swapped = registry_with(
        rules,
        hero(
            {
                "op": "seq",
                "steps": [
                    {"op": "draw", "target": "$victim", "count": 1},
                    {"op": "choose", "bind": "victim", "from": {"selector": "players"}},
                ],
            }
        ),
    )
    assert errors_matching(validate_registry(swapped), "'$victim' is not bound")


def test_for_each_binding_does_not_leak_past_its_body(rules: RuleSet) -> None:
    leaked = registry_with(
        rules,
        hero(
            {
                "op": "seq",
                "steps": [
                    {
                        "op": "for_each",
                        "over": {"selector": "opponents", "of": "$self"},
                        "bind": "foe",
                        "effect": {"op": "draw", "target": "$foe", "count": 1},
                    },
                    {"op": "draw", "target": "$foe", "count": 1},
                ],
            }
        ),
    )
    found = errors_matching(validate_registry(leaked), "'$foe' is not bound")
    assert found
    assert found[0].path.endswith("steps[1].target")


def test_filters_bind_candidate(rules: RuleSet) -> None:
    registry = registry_with(
        rules,
        hero(
            {
                "op": "discard",
                "target": "$self",
                "count": 1,
                "filter": {"op": "card_class_is", "card": "$candidate", "class": "bard"},
            }
        ),
    )
    assert errors_matching(validate_registry(registry), "not bound") == []


def test_expressions_are_scanned_for_refs(rules: RuleSet) -> None:
    registry = registry_with(
        rules, hero({"op": "draw", "target": "$self", "count": {"expr": "$ghost.hand_size"}})
    )
    found = errors_matching(validate_registry(registry), "'$ghost' is not bound")
    assert found
    assert found[0].path.endswith("count.expr")


# ---------------------------------------------------------------------------
# References into rules.yaml
# ---------------------------------------------------------------------------


def test_unknown_zone_is_caught(rules: RuleSet) -> None:
    registry = registry_with(
        rules, hero({"op": "move_card", "card": "$card", "to": {"zone": "graveyard"}})
    )
    found = errors_matching(validate_registry(registry), "unknown zone 'graveyard'")
    assert found
    assert found[0].path.endswith("to.zone")


def test_unknown_reaction_window_is_caught(rules: RuleSet) -> None:
    registry = registry_with(
        rules,
        {
            "id": "t.modifier.late",
            "kind": "modifier",
            "name": "Late",
            "reaction": {
                "window": "damage_prevention",
                "effect": {"op": "modify_roll", "amount": 1, "source": "$card"},
            },
        },
    )
    assert errors_matching(validate_registry(registry), "unknown reaction window")


def test_unknown_trigger_event_is_caught(rules: RuleSet) -> None:
    registry = registry_with(
        rules,
        hero(
            {"op": "noop"},
            triggers=[
                {
                    "on": "monster.slained",
                    "while_in": "party",
                    "effect": {"op": "draw", "target": "$self", "count": 1},
                }
            ],
        ),
    )
    found = errors_matching(validate_registry(registry), "unknown event 'monster.slained'")
    assert found
    assert found[0].hint == "did you mean 'monster.slain'?"


def test_an_event_emitted_by_a_card_counts_as_known(rules: RuleSet) -> None:
    registry = registry_with(
        rules,
        hero({"op": "emit", "event": "mod.corruption_gained"}),
        {
            "id": "t.hero.listener",
            "kind": "hero",
            "name": "Listener",
            "card_class": "wizard",
            "triggers": [
                {
                    "on": "mod.corruption_gained",
                    "while_in": "party",
                    "effect": {"op": "draw", "target": "$self", "count": 1},
                }
            ],
        },
    )
    assert errors_matching(validate_registry(registry), "unknown event") == []


# ---------------------------------------------------------------------------
# Op misuse
# ---------------------------------------------------------------------------


def test_missing_required_param(rules: RuleSet) -> None:
    registry = registry_with(rules, hero({"op": "move_card", "card": "$card"}))
    assert errors_matching(validate_registry(registry), "requires a 'to' parameter")


def test_condition_used_as_an_effect_says_so(rules: RuleSet) -> None:
    registry = registry_with(rules, hero({"op": "hand_size", "player": "$self"}))
    found = errors_matching(validate_registry(registry), "unknown effect op 'hand_size'")
    assert found
    assert found[0].hint == "'hand_size' is a condition, not an effect"


def test_unknown_param_is_a_warning_not_an_error(rules: RuleSet) -> None:
    issues = validate_registry(
        registry_with(rules, hero({"op": "draw", "target": "$self", "cont": 2}))
    )
    warnings = [i for i in issues if not i.is_error and "no parameter 'cont'" in i.message]
    assert warnings
    assert warnings[0].hint == "did you mean 'count'?"
    assert errors_matching(issues, "cont") == []


def test_invalid_comparator(rules: RuleSet) -> None:
    registry = registry_with(
        rules,
        hero(
            {
                "op": "if",
                "condition": {"op": "hand_size", "player": "$self", "cmp": "=>", "value": 1},
                "then": {"op": "noop"},
            }
        ),
    )
    assert errors_matching(validate_registry(registry), "invalid comparator")


def test_class_typo_inside_a_condition(rules: RuleSet) -> None:
    registry = registry_with(
        rules,
        hero(
            {
                "op": "if",
                "condition": {"op": "party_has_class", "player": "$self", "class": "figher"},
                "then": {"op": "noop"},
            }
        ),
    )
    found = errors_matching(validate_registry(registry), "unknown class 'figher'")
    assert found
    assert found[0].hint == "did you mean 'fighter'?"


def test_unknown_action_in_a_phase_allows_list(rules: RuleSet) -> None:
    broken = rules.model_copy(
        update={
            "phases": [p.model_copy(update={"allows": ["draw", "teleport"]}) for p in rules.phases]
        }
    )
    registry = ContentRegistry(rules=broken, cards={})
    assert errors_matching(validate_registry(registry), "unknown action 'teleport'")
