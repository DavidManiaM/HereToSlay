"""The extension seams: registering, finding, and not leaking."""

from __future__ import annotations

import pytest

from here_to_slay.core.errors import EngineError, UnknownOpError
from here_to_slay.core.events import Outcome
from here_to_slay.core.registry import (
    CONDITIONS,
    EFFECTS,
    MUTATORS,
    SELECTORS,
    Registry,
    condition,
    effect,
    registered_ops,
    temporarily,
)


class TestLookup:
    def test_unknown_op_names_itself_and_suggests(self) -> None:
        with pytest.raises(UnknownOpError, match="did you mean 'steal_hero'"):
            EFFECTS.get("stael_hero")

    def test_unknown_op_with_no_near_match_points_at_plugins(self) -> None:
        with pytest.raises(UnknownOpError, match=r"plugin\.py"):
            EFFECTS.get("summon_kraken")

    def test_registering_twice_is_an_error(self) -> None:
        registry: Registry[int] = Registry("thing")
        registry.register("x", 1)
        with pytest.raises(EngineError, match="already registered"):
            registry.register("x", 2)
        registry.register("x", 2, replace=True)
        assert registry.get("x") == 2


class TestDecorators:
    def test_a_plain_function_is_wrapped_into_a_generator(self) -> None:
        """A simple op should not have to fake a ``yield`` to be callable."""
        with temporarily():

            @effect("test_plain")
            def handler(ctx, params):  # type: ignore[no-untyped-def]
                return Outcome.DONE

            flow = EFFECTS.get("test_plain")(None, {})  # type: ignore[arg-type]
            with pytest.raises(StopIteration) as stop:
                next(flow)
            assert stop.value.value is Outcome.DONE

    def test_a_generator_condition_is_refused(self) -> None:
        """A predicate that could suspend would make "is this legal?" have
        side effects."""
        with temporarily(), pytest.raises(EngineError, match="must answer"):

            @condition("test_asking")
            def handler(ctx, params):  # type: ignore[no-untyped-def]
                yield None

    def test_temporarily_restores_every_registry(self) -> None:
        before = registered_ops()
        with temporarily():

            @effect("test_scratch")
            def handler(ctx, params):  # type: ignore[no-untyped-def]
                return Outcome.DONE

            assert "test_scratch" in EFFECTS
        assert "test_scratch" not in EFFECTS
        assert registered_ops() == before


class TestCatalogue:
    """The base catalogue has to actually be there before Phase 4 leans on it."""

    @pytest.mark.parametrize(
        "op",
        ["seq", "if", "choose", "for_each", "repeat", "optional", "noop", "draw", "discard"],
    )
    def test_core_effects_are_registered(self, op: str) -> None:
        assert op in EFFECTS

    @pytest.mark.parametrize("op", ["all", "any", "not", "compare", "has_card", "party_has_class"])
    def test_core_conditions_are_registered(self, op: str) -> None:
        assert op in CONDITIONS

    @pytest.mark.parametrize("name", ["players", "opponents", "cards", "heroes", "monster_row"])
    def test_core_selectors_are_registered(self, name: str) -> None:
        assert name in SELECTORS

    def test_every_state_changing_event_has_exactly_one_mutator(self) -> None:
        for name in ("card.moved", "card.drawn", "card.discarded", "flag.changed", "player.won"):
            assert name in MUTATORS
