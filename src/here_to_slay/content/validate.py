"""The semantic pass (``docs/card_schemas.md §8``).

Pydantic proves a pack is *shaped* right. This proves it *means* something:

1. every ``op`` exists in the vocabulary, with its required params
2. every ``$ref`` is bindable in its lexical scope
3. every trigger's ``on:`` event is a core event or emitted by some loaded card
4. every ``card_class`` is declared in ``rules.classes``; art files exist (warning)
5. deck arithmetic is sane
6. roll bands cover the whole dice range
7. zones, windows and actions referenced by content actually exist

A modder gets a path-qualified table, not a ``KeyError`` twenty minutes in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from here_to_slay.content.errors import ContentIssue, Severity
from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.schema import (
    AbilityDef,
    Band,
    EquipDef,
    PlayDef,
    ReactionDef,
    RollDef,
    RuleSet,
    TargetDef,
    TriggerDef,
    WindowDef,
)
from here_to_slay.content.vocabulary import (
    BASE_VOCABULARY,
    CORE_REFS,
    FILTER_REF,
    VALID_CMP,
    VALID_SCOPES,
    WINDOW_ORDERS,
    OpKind,
    OpSpec,
    Role,
    Vocabulary,
)

REF_IN_EXPR = re.compile(r"\$([a-z_][a-z0-9_]*)")

Scope = frozenset[str]


def _as_dict(node: Any) -> dict[str, Any]:
    if isinstance(node, BaseModel):
        return node.model_dump(mode="python")
    if isinstance(node, dict):
        return dict(node)
    return {}


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _did_you_mean(name: str, candidates: Iterable[str]) -> str | None:
    import difflib

    matches = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.7)
    return f"did you mean '{matches[0]}'?" if matches else None


class _Validator:
    """Walks the whole registry once, accumulating issues."""

    def __init__(
        self,
        registry: ContentRegistry,
        vocabulary: Vocabulary,
        *,
        check_art: bool = True,
    ) -> None:
        self.registry = registry
        self.rules: RuleSet = registry.rules
        self.vocab = vocabulary
        self.check_art = check_art
        self.issues: list[ContentIssue] = []
        self.classes = set(self.rules.classes)
        self.zone_ids = self.rules.zone_ids
        self.action_ids = {action.id for action in self.rules.actions}
        self.emitted_events = _collect_emitted_events(registry)
        self.assets_roots = _assets_roots(registry)

    # -- reporting ---------------------------------------------------------

    def error(self, path: str, message: str, hint: str | None = None) -> None:
        self.issues.append(ContentIssue(path, message, Severity.ERROR, hint))

    def warn(self, path: str, message: str, hint: str | None = None) -> None:
        self.issues.append(ContentIssue(path, message, Severity.WARNING, hint))

    # -- entry point -------------------------------------------------------

    def run(self) -> list[ContentIssue]:
        self.check_rules()
        for card_id in sorted(self.registry.cards):
            self.check_card(card_id)
        self.check_deck_arithmetic()
        return self.issues

    # -- rules -------------------------------------------------------------

    def check_rules(self) -> None:
        path = "rules"
        if not self.classes:
            self.warn(f"{path}.classes", "no classes declared")
        if not self.zone_ids:
            self.error(f"{path}.zones", "no zones declared — the engine has nowhere to put cards")

        seen_zones: set[str] = set()
        for index, zone in enumerate(self.rules.zones):
            if zone.id in seen_zones:
                self.error(f"{path}.zones[{index}].id", f"duplicate zone id '{zone.id}'")
            seen_zones.add(zone.id)

        scope: Scope = frozenset({*CORE_REFS})
        for index, action in enumerate(self.rules.actions):
            base = f"{path}.actions[{index}]"
            if action.requires is not None:
                self.condition(action.requires, f"{base}.requires", scope)
            for target_index, target in enumerate(action.targets):
                self.check_target(target, f"{base}.targets[{target_index}]", scope)
            if action.effect is not None:
                self.effect(action.effect, f"{base}.effect", scope)

        for index, step in enumerate(self.rules.turn.after_action):
            self.effect(step, f"{path}.turn.after_action[{index}]", scope)

        for name, window in sorted(self.rules.windows.items()):
            self.check_window(name, window, f"{path}.windows.{name}", scope)

        for index, phase in enumerate(self.rules.phases):
            base = f"{path}.phases[{index}]"
            for step_index, step in enumerate(phase.on_enter):
                self.effect(step, f"{base}.on_enter[{step_index}]", scope)
            for step_index, step in enumerate(phase.on_exit):
                self.effect(step, f"{base}.on_exit[{step_index}]", scope)
            if phase.loop_while is not None:
                self.condition(phase.loop_while, f"{base}.loop_while", scope)
            for allow_index, action_id in enumerate(phase.allows):
                if action_id not in self.action_ids:
                    self.error(
                        f"{base}.allows[{allow_index}]",
                        f"unknown action '{action_id}'",
                        _did_you_mean(action_id, self.action_ids),
                    )

        for index, victory in enumerate(self.rules.victory):
            self.condition(victory.condition, f"{path}.victory[{index}].condition", scope)

        if not self.rules.victory:
            self.warn(f"{path}.victory", "no victory conditions — the game cannot be won")

    # -- cards -------------------------------------------------------------

    def check_card(self, card_id: str) -> None:
        card = self.registry.cards[card_id]
        base = self.registry.source_of(card_id)
        scope: Scope = frozenset({*CORE_REFS})

        if card.pack_id not in self.registry.pack_ids:
            self.warn(
                f"{base}.id",
                f"id is namespaced to pack '{card.pack_id}', which is not loaded",
                hint="ids should read '<pack>.<kind>.<slug>'",
            )

        card_class = getattr(card, "card_class", None)
        if card_class is not None and card_class not in self.classes:
            self.error(
                f"{base}.card_class",
                f"unknown class '{card_class}'",
                _did_you_mean(card_class, self.classes)
                or f"declared classes: {sorted(self.classes)}",
            )

        if (
            card.art
            and self.check_art
            and self.assets_roots
            and not any((root / card.art).is_file() for root in self.assets_roots)
        ):
            self.warn(f"{base}.art", f"art file not found: '{card.art}'")

        for index, trigger in enumerate(card.triggers):
            self.check_trigger(trigger, f"{base}.triggers[{index}]", scope)

        ability = getattr(card, "ability", None)
        if isinstance(ability, AbilityDef):
            self.check_ability(ability, f"{base}.ability", scope)

        play = getattr(card, "play", None)
        if isinstance(play, PlayDef):
            self.check_play(play, f"{base}.play", scope)

        equip = getattr(card, "equip", None)
        if isinstance(equip, EquipDef):
            self.selector(equip.to, f"{base}.equip.to", scope)
            if equip.effect is not None:
                self.effect(equip.effect, f"{base}.equip.effect", scope)

        reaction = getattr(card, "reaction", None)
        if isinstance(reaction, ReactionDef):
            self.check_reaction(reaction, f"{base}.reaction", scope)

        requirement = getattr(card, "requirement", None)
        if requirement is not None:
            self.condition(requirement, f"{base}.requirement", scope)

        roll = getattr(card, "roll", None)
        if isinstance(roll, RollDef):
            self.check_roll(roll, f"{base}.roll", scope)

        on_slay = getattr(card, "on_slay", None)
        if on_slay is not None:
            self.effect(on_slay, f"{base}.on_slay", scope)

    def check_target(self, target: TargetDef, path: str, scope: Scope) -> None:
        """An action's target list is what ``legal_intents()`` expands, so a
        broken selector here is a menu that silently loses a legal move."""
        self.selector(target.source, f"{path}.from", scope)
        if target.where is not None:
            self.condition(target.where, f"{path}.where", scope | {FILTER_REF})

    def check_window(self, name: str, window: WindowDef, path: str, scope: Scope) -> None:
        if window.order not in WINDOW_ORDERS:
            self.error(
                f"{path}.order",
                f"unknown window order '{window.order}'",
                _did_you_mean(window.order, WINDOW_ORDERS) or f"one of {sorted(WINDOW_ORDERS)}",
            )
        if window.on is None:
            if window.condition is not None:
                self.warn(
                    f"{path}.condition",
                    "a window with no 'on' event never opens by itself, so its "
                    "condition is never evaluated",
                )
            return
        for index, event in enumerate(window.opens_on):
            if self.vocab.knows_event(event) or event in self.emitted_events:
                continue
            where = f"{path}.on" if isinstance(window.on, str) else f"{path}.on[{index}]"
            self.error(
                where,
                f"unknown event '{event}'",
                _did_you_mean(event, self.vocab.events)
                or "no loaded card emits it either — see architecture_notes.md §3.3",
            )
        if window.condition is not None:
            self.condition(window.condition, f"{path}.condition", scope)

    def check_trigger(self, trigger: TriggerDef, path: str, scope: Scope) -> None:
        if not self.vocab.knows_event(trigger.on) and trigger.on not in self.emitted_events:
            self.error(
                f"{path}.on",
                f"unknown event '{trigger.on}'",
                _did_you_mean(trigger.on, self.vocab.events)
                or "no loaded card emits it either — see architecture_notes.md §3.3",
            )
        if trigger.while_in not in self.zone_ids:
            self.error(
                f"{path}.while_in",
                f"unknown zone '{trigger.while_in}'",
                _did_you_mean(trigger.while_in, self.zone_ids),
            )
        if trigger.condition is not None:
            self.condition(trigger.condition, f"{path}.condition", scope)
        self.effect(trigger.effect, f"{path}.effect", scope)

    def check_ability(self, ability: AbilityDef, path: str, scope: Scope) -> None:
        self.check_cost(ability.cost, f"{path}.cost")
        if ability.roll is not None:
            self.check_roll(ability.roll, f"{path}.roll", scope)
        if ability.effect is not None:
            self.effect(ability.effect, f"{path}.effect", scope)

    def check_play(self, play: PlayDef, path: str, scope: Scope) -> None:
        self.check_cost(play.cost, f"{path}.cost")
        if play.roll is not None:
            self.check_roll(play.roll, f"{path}.roll", scope)
        if play.effect is not None:
            self.effect(play.effect, f"{path}.effect", scope)
        if play.then is not None:
            self.effect(play.then, f"{path}.then", scope)
        if play.effect is None and play.roll is None:
            self.warn(f"{path}", "play block does nothing (no effect, no roll)")

    def check_reaction(self, reaction: ReactionDef, path: str, scope: Scope) -> None:
        if reaction.window not in self.rules.windows:
            self.error(
                f"{path}.window",
                f"unknown reaction window '{reaction.window}'",
                _did_you_mean(reaction.window, self.rules.windows)
                or "declare it under rules.windows",
            )
        if reaction.condition is not None:
            self.condition(reaction.condition, f"{path}.condition", scope)
        self.effect(reaction.effect, f"{path}.effect", scope)

    def check_cost(self, cost: dict[str, Any], path: str) -> None:
        for key, value in cost.items():
            if isinstance(value, int | float | str) or value is None:
                continue
            self.error(f"{path}.{key}", "cost values must be scalars")

    def check_roll(self, roll: RollDef, path: str, scope: Scope) -> None:
        if roll.roller is not None:
            self.value(roll.roller, f"{path}.roller", scope)
        self.check_bands(roll.outcomes, roll, f"{path}.outcomes", scope)

    def check_bands(
        self, bands: Sequence[Band], roll: RollDef | None, path: str, scope: Scope
    ) -> None:
        for index, band in enumerate(bands):
            self.effect(band.effect, f"{path}[{index}].effect", scope)
        if roll is None or not bands:
            if not bands:
                self.warn(path, "roll declares no outcome bands")
            return
        low, high = roll.range
        uncovered = [
            total
            for total in range(low, high + 1)
            if not any(band.matches(total) for band in bands)
        ]
        if uncovered:
            self.error(
                path,
                f"bands do not cover every possible total of {roll.dice}: "
                f"{_summarise(uncovered)} unmatched",
                hint=f"{roll.dice} rolls {low}..{high}; add a catch-all band or widen min/max",
            )

    # -- deck arithmetic ---------------------------------------------------

    def check_deck_arithmetic(self) -> None:
        if not self.registry.cards:
            self.warn("cards", "pack declares no cards")
            return
        totals = self.registry.deck_composition()
        setup = self.rules.setup
        leaders = totals.get("leader_pool", 0)
        monsters = totals.get("monster_deck", 0)
        main = totals.get("main_deck", 0)

        if monsters == 0:
            self.warn("cards", "no monster cards — the monster deck would be empty")
        elif monsters < setup.monster_row_size:
            self.error(
                "cards",
                f"{monsters} monster card(s) cannot fill a monster row of {setup.monster_row_size}",
            )
        if leaders < setup.max_players:
            self.warn(
                "cards",
                f"{leaders} party leader(s) for up to {setup.max_players} players",
            )
        needed = setup.starting_hand * setup.max_players
        if main < needed:
            self.warn(
                "cards",
                f"main deck holds {main} card(s); dealing {setup.max_players} players "
                f"{setup.starting_hand} each needs {needed}",
            )

    # -- the op walkers ----------------------------------------------------

    def effect(self, node: Any, path: str, scope: Scope) -> Scope:
        """Validate an effect node. Returns bindings it exports to later siblings."""
        data = _as_dict(node)
        if not data:
            self.error(path, "expected an effect node like {op: noop}")
            return frozenset()
        op = data.get("op")
        if not isinstance(op, str):
            self.error(path, "effect node is missing a string 'op'")
            return frozenset()

        spec = self.vocab.effect(op)
        if spec is None:
            hint = _did_you_mean(op, self.vocab.effects)
            if self.vocab.condition(op) is not None:
                hint = f"'{op}' is a condition, not an effect"
            self.error(f"{path}.op", f"unknown effect op '{op}'", hint)
            return frozenset()

        self.check_params(spec, data, path, scope)
        exported = self.walk_params(spec, data, path, scope)
        return exported

    def condition(self, node: Any, path: str, scope: Scope) -> None:
        data = _as_dict(node)
        if not data:
            self.error(path, "expected a condition node like {op: always}")
            return
        op = data.get("op")
        if not isinstance(op, str):
            self.error(path, "condition node is missing a string 'op'")
            return

        spec = self.vocab.condition(op)
        if spec is None:
            hint = _did_you_mean(op, self.vocab.conditions)
            if self.vocab.effect(op) is not None:
                hint = f"'{op}' is an effect, not a condition"
            self.error(f"{path}.op", f"unknown condition op '{op}'", hint)
            return

        self.check_params(spec, data, path, scope)
        self.walk_params(spec, data, path, scope)

    def selector(self, node: Any, path: str, scope: Scope) -> None:
        if isinstance(node, str):
            self.value(node, path, scope)
            return
        data = _as_dict(node)
        name = data.get("selector")
        if not isinstance(name, str):
            self.error(path, "expected a $ref or a {selector: ...} node")
            return
        spec = self.vocab.selector(name)
        if spec is None:
            self.error(
                f"{path}.selector",
                f"unknown selector '{name}'",
                _did_you_mean(name, self.vocab.selectors),
            )
            return
        self.walk_params(spec, data, path, scope, skip={"selector"})

    def zone(self, node: Any, path: str, scope: Scope) -> None:
        if isinstance(node, str):
            zone_id = node
        elif isinstance(node, dict):
            self.value(node.get("player"), _join(path, "player"), scope)
            zone_id = node.get("zone")  # type: ignore[assignment]
            if not isinstance(zone_id, str):
                self.error(path, "zone reference needs a 'zone' key")
                return
        else:
            self.error(path, "expected a zone reference like {zone: discard}")
            return
        if zone_id.startswith("$"):
            self.value(zone_id, path, scope)
        elif zone_id not in self.zone_ids:
            self.error(
                _join(path, "zone") if isinstance(node, dict) else path,
                f"unknown zone '{zone_id}'",
                _did_you_mean(zone_id, self.zone_ids) or f"declared zones: {sorted(self.zone_ids)}",
            )

    def value(self, raw: Any, path: str, scope: Scope) -> None:
        """Check every ``$ref`` reachable from a parameter value."""
        if raw is None or isinstance(raw, bool | int | float):
            return
        if isinstance(raw, str):
            if raw.startswith("$"):
                self.check_ref(raw, path, scope)
            return
        if isinstance(raw, dict):
            expr = raw.get("expr")
            if isinstance(expr, str):
                for name in REF_IN_EXPR.findall(expr):
                    self.check_ref(f"${name}", _join(path, "expr"), scope)
                return
            if "selector" in raw:
                self.selector(raw, path, scope)
                return
            for key, item in raw.items():
                self.value(item, _join(path, str(key)), scope)
            return
        if isinstance(raw, list):
            for index, item in enumerate(raw):
                self.value(item, f"{path}[{index}]", scope)

    def check_ref(self, ref: str, path: str, scope: Scope) -> None:
        root = ref.removeprefix("$").split(".", 1)[0]
        if not root:
            self.error(path, f"malformed reference {ref!r}")
            return
        if root not in scope:
            self.error(
                path,
                f"reference '${root}' is not bound here",
                _did_you_mean(root, scope)
                or "bind it with a 'choose'/'for_each' step before this one",
            )

    # -- param dispatch ----------------------------------------------------

    def check_params(self, spec: OpSpec, data: dict[str, Any], path: str, scope: Scope) -> None:
        for name in spec.required_params:
            if data.get(name) is None:
                self.error(f"{path}.{name}", f"'{spec.name}' requires a '{name}' parameter")
        known = {"op", *spec.params}
        for name in data:
            if name not in known:
                self.warn(
                    f"{path}.{name}",
                    f"'{spec.name}' has no parameter '{name}' — it will be ignored",
                    _did_you_mean(name, spec.params),
                )
        cmp_value = data.get("cmp")
        if cmp_value is not None and cmp_value not in VALID_CMP:
            self.error(
                f"{path}.cmp", f"invalid comparator {cmp_value!r}", f"one of {sorted(VALID_CMP)}"
            )
        scope_value = data.get("scope")
        if "scope" in spec.params and scope_value is not None and scope_value not in VALID_SCOPES:
            self.error(
                f"{path}.scope",
                f"invalid flag scope {scope_value!r}",
                f"one of {sorted(VALID_SCOPES)}",
            )
        class_value = data.get("class")
        if isinstance(class_value, str) and self.classes and class_value not in self.classes:
            self.error(
                f"{path}.class",
                f"unknown class '{class_value}'",
                _did_you_mean(class_value, self.classes),
            )

    def walk_params(
        self,
        spec: OpSpec,
        data: dict[str, Any],
        path: str,
        scope: Scope,
        *,
        skip: set[str] | None = None,
    ) -> Scope:
        """Dispatch each param by its declared role. Returns exported bindings."""
        skipped = {"op", *(skip or set())}
        body_scope = scope
        if spec.binds and spec.bind_scope == "body":
            name = data.get(spec.binds)
            if isinstance(name, str):
                body_scope = scope | {name}

        for name, raw in data.items():
            if name in skipped:
                continue
            role = spec.role_of(name)
            inner = body_scope if name in spec.body else scope
            self.walk_param(role, raw, _join(path, name), inner)

        if spec.binds and spec.bind_scope == "siblings":
            name = data.get(spec.binds)
            if isinstance(name, str):
                return frozenset({name})
        return frozenset()

    def walk_param(self, role: Role, raw: Any, path: str, scope: Scope) -> None:
        match role:
            case Role.EFFECT:
                self.effect(raw, path, scope)
            case Role.EFFECT_LIST:
                self.effect_sequence(raw, path, scope)
            case Role.CONDITION:
                self.condition(raw, path, scope)
            case Role.CONDITION_LIST:
                for index, item in enumerate(raw if isinstance(raw, list) else [raw]):
                    self.condition(item, f"{path}[{index}]", scope)
            case Role.FILTER:
                self.condition(raw, path, scope | {FILTER_REF})
            case Role.SELECTOR:
                self.selector(raw, path, scope)
            case Role.ZONE:
                self.zone(raw, path, scope)
            case Role.BAND_LIST:
                self.band_list(raw, path, scope)
            case Role.OPTION_LIST:
                self.option_list(raw, path, scope)
            case Role.ROLL_SPEC:
                self.value(raw, path, scope)
            case Role.NAME:
                if not isinstance(raw, str):
                    self.error(path, "expected a plain name (no '$')")
                elif raw.startswith("$"):
                    self.error(path, f"expected a plain name, got the reference {raw!r}")
            case _:
                self.value(raw, path, scope)

    def effect_sequence(self, raw: Any, path: str, scope: Scope) -> None:
        """Walk ``steps``, threading bindings from each step into the next."""
        if not isinstance(raw, list):
            self.error(path, "expected a list of effects")
            return
        running = scope
        for index, step in enumerate(raw):
            running = running | self.effect(step, f"{path}[{index}]", running)

    def band_list(self, raw: Any, path: str, scope: Scope) -> None:
        if not isinstance(raw, list):
            self.error(path, "expected a list of outcome bands")
            return
        for index, band in enumerate(raw):
            data = _as_dict(band)
            if "effect" not in data:
                self.error(f"{path}[{index}]", "outcome band needs an 'effect'")
                continue
            self.effect(data["effect"], f"{path}[{index}].effect", scope)

    def option_list(self, raw: Any, path: str, scope: Scope) -> None:
        if not isinstance(raw, list):
            self.error(path, "expected a list of {label, effect} options")
            return
        for index, option in enumerate(raw):
            data = _as_dict(option)
            where = f"{path}[{index}]"
            if not data.get("label"):
                self.warn(f"{where}.label", "option has no label to show the player")
            if data.get("condition") is not None:
                self.condition(data["condition"], f"{where}.condition", scope)
            if "effect" not in data:
                self.error(where, "option needs an 'effect'")
                continue
            self.effect(data["effect"], f"{where}.effect", scope)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarise(values: Sequence[int], limit: int = 6) -> str:
    shown = ", ".join(str(v) for v in values[:limit])
    return shown if len(values) <= limit else f"{shown}, ..."


def _collect_emitted_events(registry: ContentRegistry) -> set[str]:
    """Event names any loaded card announces with ``op: emit``."""
    events: set[str] = set()

    def scan(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("op") == "emit" and isinstance(node.get("event"), str):
                events.add(node["event"])
            for value in node.values():
                scan(value)
        elif isinstance(node, list):
            for item in node:
                scan(item)

    for card in registry.cards.values():
        scan(card.model_dump(mode="python"))
    scan(registry.rules.model_dump(mode="python"))
    return events


def _assets_roots(registry: ContentRegistry) -> tuple[Path, ...]:
    roots: list[Path] = []
    for pack_root in registry.roots:
        for candidate in (pack_root / "assets", pack_root.parent / "assets"):
            if candidate.is_dir():
                roots.append(candidate)
    cwd_assets = Path.cwd() / "assets"
    if cwd_assets.is_dir():
        roots.append(cwd_assets)
    return tuple(dict.fromkeys(roots))


def validate_registry(
    registry: ContentRegistry,
    *,
    vocabulary: Vocabulary = BASE_VOCABULARY,
    check_art: bool = True,
) -> list[ContentIssue]:
    """Run the semantic pass. Returns every issue found, errors and warnings."""
    return _Validator(registry, vocabulary, check_art=check_art).run()


__all__ = ["OpKind", "validate_registry"]
