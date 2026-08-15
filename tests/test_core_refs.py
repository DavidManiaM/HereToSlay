"""``$ref`` splitting, path walking, and the expression evaluator."""

from __future__ import annotations

import pytest

from here_to_slay.core.errors import EffectError
from here_to_slay.core.refs import (
    evaluate_expression,
    follow_path,
    is_ref,
    member,
    refs_in,
    split_ref,
)


def resolver(values: dict[str, object]):
    def resolve(ref: str) -> object:
        return values[ref]

    return resolve


class TestSplitting:
    def test_plain_reference(self) -> None:
        assert split_ref("$self") == ("self", ())

    def test_dotted_reference(self) -> None:
        assert split_ref("$event.player") == ("event", ("player",))
        assert split_ref("$rules.turn.action_points_per_turn") == (
            "rules",
            ("turn", "action_points_per_turn"),
        )

    def test_is_ref(self) -> None:
        assert is_ref("$self")
        assert not is_ref("self")
        assert not is_ref(3)

    @pytest.mark.parametrize("bad", ["$", "$1x", "$a..b", "$a.", "self"])
    def test_malformed_references_are_rejected(self, bad: str) -> None:
        with pytest.raises(EffectError):
            split_ref(bad)


class TestPaths:
    def test_mapping_then_attribute(self) -> None:
        assert member({"player": "p1"}, "player") == "p1"

    def test_sequence_index(self) -> None:
        assert member(["a", "b"], "1") == "b"

    def test_missing_member_names_the_path(self) -> None:
        with pytest.raises(EffectError, match="has no 'nope'"):
            member({"player": "p1"}, "nope")

    def test_follow_walks_several_steps(self) -> None:
        value = {"a": {"b": {"c": 7}}}
        assert follow_path(value, ("a", "b", "c")) == 7

    def test_deref_is_applied_between_steps(self) -> None:
        """``$card.attached_to`` needs the id turned back into the card."""
        cards = {"sword#1": {"attached_to": "hero#1"}}
        assert follow_path("sword#1", ("attached_to",), deref=cards.get) == "hero#1"


class TestExpressions:
    def test_a_lone_reference_passes_through_untouched(self) -> None:
        assert evaluate_expression("$self", resolver({"$self": "p1"})) == "p1"

    def test_arithmetic(self) -> None:
        resolve = resolver({"$ap": 3})
        assert evaluate_expression("$ap + 1", resolve) == 4
        assert evaluate_expression("2 * ($ap - 1)", resolve) == 4
        assert evaluate_expression("-$ap", resolve) == -3
        assert evaluate_expression("7 / 2", resolve) == 3  # whole cards only
        assert evaluate_expression("7 % 2", resolve) == 1

    def test_a_list_reference_counts(self) -> None:
        assert evaluate_expression("$hand * 2", resolver({"$hand": ["a", "b", "c"]})) == 6

    def test_division_by_zero_is_an_effect_error(self) -> None:
        with pytest.raises(EffectError, match="division by zero"):
            evaluate_expression("1 / 0", resolver({}))

    def test_non_numeric_reference_in_arithmetic_is_rejected(self) -> None:
        with pytest.raises(EffectError, match="not a number"):
            evaluate_expression("$self + 1", resolver({"$self": "p1"}))

    @pytest.mark.parametrize("bad", ["1 +", "(1", "1 ** 2", "__import__('os')"])
    def test_nothing_but_arithmetic_parses(self, bad: str) -> None:
        with pytest.raises(EffectError):
            evaluate_expression(bad, resolver({}))

    def test_refs_in_finds_every_reference(self) -> None:
        assert refs_in("$a + $b.c * 2") == ("$a", "$b.c")
