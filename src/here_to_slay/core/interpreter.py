"""The generator driver, and the Request/Decision protocol it speaks.

This is the answer to the hardest constraint in the project
(``docs/architecture_notes.md §4``): the engine must *ask questions* — "which
Hero do you steal?", "anyone want to play a Modifier?" — but must never block,
because the same engine runs under a CLI ``input()``, a pygame frame loop, an
AI policy and a test harness.

Effects are Python generators. An effect ``yield``s a :class:`Request` and
receives the answer; ``yield from`` composes the entire call stack into one
generator, so a Challenge landing three levels deep suspends the *whole* pending
computation inside the Python frame itself — no continuation objects, no state
machine per effect.

The driver loop the outside world writes is four lines::

    status = interpreter.begin(flow)
    while isinstance(status, Awaiting):
        status = interpreter.submit(presenter.answer(status.request))

Two rules that are not negotiable:

* **Every request carries its legal option set, and every decision is
  re-validated on submit.** The UI is never trusted; an illegal decision raises
  :class:`IllegalDecisionError` rather than corrupting state.
* **Every accepted decision is appended to the log.** ``(content_hash, seed,
  log)`` reproduces the game exactly — replay, network play, golden tests and
  bug reports all fall out of that one property.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from here_to_slay.core.errors import EngineError, IllegalDecisionError
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import CardId, PlayerId, ZoneId
from here_to_slay.core.invariants import check_if_strict
from here_to_slay.core.state import GameState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.log import DecisionLog

#: what a reaction prompt means by "I'll pass" — ``ReactionChosen(PASS)``
PASS: None = None


# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Intent:
    """A player's declared action: "play *this* Hero", "attack *that* Monster".

    Kept deliberately thin — an action id plus the things it points at. What is
    *legal* is computed by the engine from ``rules.actions`` (Phase 4), never by
    the UI, so this is only ever a carrier.
    """

    action: str
    card: CardId | None = None
    target: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""

    def key(self) -> str:
        """A stable identity for validating a submitted intent against the
        offered set, and for the decision log."""
        return "|".join(
            (
                self.action,
                self.card or "",
                self.target or "",
                ",".join(f"{k}={v}" for k, v in sorted(self.params.items())),
            )
        )

    def as_data(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "card": self.card,
            "target": self.target,
            "params": dict(self.params),
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Intent:
        return cls(
            action=data["action"],
            card=data.get("card"),
            target=data.get("target"),
            params=dict(data.get("params") or {}),
        )

    def __str__(self) -> str:
        return self.label or self.key()


@dataclass(frozen=True, slots=True)
class Option:
    """One labelled branch of a ``choose_effect``, or a playable reaction."""

    key: str
    label: str
    card: CardId | None = None

    def as_data(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "card": self.card}


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Request:
    """A question the engine is suspended on.

    ``requester`` is who must answer — the presenter uses it to solicit the
    right player and, in a hot-seat CLI, to clear the screen first.
    """

    kind: ClassVar[str] = "request"

    requester: PlayerId
    prompt: str = ""

    def validate(self, decision: Decision) -> Any:
        """Check a decision against *this* request; return what to resume with."""
        raise NotImplementedError

    def _reject(self, message: str) -> IllegalDecisionError:
        return IllegalDecisionError(
            f"{type(self).__name__} for '{self.requester}': {message}"
            + (f" (prompt: {self.prompt})" if self.prompt else "")
        )

    def _expect(self, decision: Decision, wanted: type[Decision]) -> Any:
        if not isinstance(decision, wanted):
            raise self._reject(
                f"expected a {wanted.__name__}, got {type(decision).__name__}"
            )
        return decision

    def as_data(self) -> dict[str, Any]:
        return {"kind": self.kind, "requester": self.requester, "prompt": self.prompt}


@dataclass(frozen=True, slots=True)
class ChooseCards(Request):
    """Pick between ``minimum`` and ``maximum`` cards out of ``candidates``."""

    kind: ClassVar[str] = "choose_cards"

    candidates: tuple[CardId, ...] = ()
    minimum: int = 1
    maximum: int = 1
    from_zone: ZoneId | None = None
    #: candidates the chooser may not see the faces of (their own blind pick)
    hidden: bool = False

    def validate(self, decision: Decision) -> tuple[CardId, ...]:
        chosen = self._expect(decision, CardsChosen).cards
        if len(chosen) < self.minimum or len(chosen) > self.maximum:
            raise self._reject(
                f"chose {len(chosen)} card(s), but this asks for "
                f"{self.minimum}..{self.maximum}"
            )
        if len(set(chosen)) != len(chosen):
            raise self._reject(f"the same card was chosen twice: {list(chosen)}")
        for card in chosen:
            if card not in self.candidates:
                raise self._reject(f"'{card}' is not among the candidates")
        return tuple(chosen)

    def as_data(self) -> dict[str, Any]:
        return {
            **super().as_data(),
            "candidates": list(self.candidates),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "from_zone": self.from_zone,
            "hidden": self.hidden,
        }


@dataclass(frozen=True, slots=True)
class ChoosePlayer(Request):
    kind: ClassVar[str] = "choose_player"

    candidates: tuple[PlayerId, ...] = ()

    def validate(self, decision: Decision) -> PlayerId:
        chosen = self._expect(decision, PlayerChosen).player
        if chosen not in self.candidates:
            raise self._reject(f"'{chosen}' is not among {list(self.candidates)}")
        return chosen

    def as_data(self) -> dict[str, Any]:
        return {**super().as_data(), "candidates": list(self.candidates)}


@dataclass(frozen=True, slots=True)
class ChooseOption(Request):
    """Pick one labelled branch. Used by ``choose_effect`` and by menus."""

    kind: ClassVar[str] = "choose_option"

    options: tuple[Option, ...] = ()

    def validate(self, decision: Decision) -> str:
        chosen = self._expect(decision, OptionChosen).key
        if chosen not in {option.key for option in self.options}:
            raise self._reject(
                f"'{chosen}' is not an offered option "
                f"({[option.key for option in self.options]})"
            )
        return chosen

    def as_data(self) -> dict[str, Any]:
        return {**super().as_data(), "options": [option.as_data() for option in self.options]}


@dataclass(frozen=True, slots=True)
class Confirm(Request):
    kind: ClassVar[str] = "confirm"

    def validate(self, decision: Decision) -> bool:
        return bool(self._expect(decision, Confirmed).ok)


@dataclass(frozen=True, slots=True)
class ChooseIntent(Request):
    """The main-phase menu: everything this seat may legally do right now."""

    kind: ClassVar[str] = "choose_intent"

    intents: tuple[Intent, ...] = ()

    def validate(self, decision: Decision) -> Intent:
        chosen = self._expect(decision, IntentChosen).intent
        legal = {intent.key(): intent for intent in self.intents}
        if chosen.key() not in legal:
            raise self._reject(f"'{chosen}' is not a legal intent right now")
        # Return the engine's own copy: the UI's version is untrusted data.
        return legal[chosen.key()]

    def as_data(self) -> dict[str, Any]:
        return {**super().as_data(), "intents": [intent.as_data() for intent in self.intents]}


@dataclass(frozen=True, slots=True)
class ReactionPrompt(Request):
    """"Anyone want to respond?" — always includes an explicit pass."""

    kind: ClassVar[str] = "reaction"

    window: str = ""
    options: tuple[Option, ...] = ()

    def validate(self, decision: Decision) -> CardId | None:
        chosen = self._expect(decision, ReactionChosen).card
        if chosen is None:
            return None
        offered = {option.card for option in self.options}
        if chosen not in offered:
            raise self._reject(f"'{chosen}' is not playable into the '{self.window}' window")
        return chosen

    def as_data(self) -> dict[str, Any]:
        return {
            **super().as_data(),
            "window": self.window,
            "options": [option.as_data() for option in self.options],
        }


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decision:
    """An answer. Serialisable, because the log is the game."""

    kind: ClassVar[str] = "decision"

    def as_data(self) -> dict[str, Any]:
        return {}

    @classmethod
    def from_data(cls, kind: str, data: dict[str, Any]) -> Decision:
        try:
            builder = _DECISION_KINDS[kind]
        except KeyError:
            raise EngineError(f"unknown decision kind '{kind}' in log") from None
        return builder(data)


@dataclass(frozen=True, slots=True)
class CardsChosen(Decision):
    kind: ClassVar[str] = "cards_chosen"

    cards: tuple[CardId, ...] = ()

    def as_data(self) -> dict[str, Any]:
        return {"cards": list(self.cards)}


@dataclass(frozen=True, slots=True)
class PlayerChosen(Decision):
    kind: ClassVar[str] = "player_chosen"

    player: PlayerId = ""  # type: ignore[assignment]

    def as_data(self) -> dict[str, Any]:
        return {"player": self.player}


@dataclass(frozen=True, slots=True)
class OptionChosen(Decision):
    kind: ClassVar[str] = "option_chosen"

    key: str = ""

    def as_data(self) -> dict[str, Any]:
        return {"key": self.key}


@dataclass(frozen=True, slots=True)
class Confirmed(Decision):
    kind: ClassVar[str] = "confirmed"

    ok: bool = False

    def as_data(self) -> dict[str, Any]:
        return {"ok": self.ok}


@dataclass(frozen=True, slots=True)
class IntentChosen(Decision):
    kind: ClassVar[str] = "intent_chosen"

    intent: Intent = Intent(action="")

    def as_data(self) -> dict[str, Any]:
        return {"intent": self.intent.as_data()}


@dataclass(frozen=True, slots=True)
class ReactionChosen(Decision):
    kind: ClassVar[str] = "reaction_chosen"

    #: ``PASS`` (that is, ``None``) means "no thanks"
    card: CardId | None = PASS

    def as_data(self) -> dict[str, Any]:
        return {"card": self.card}


_DECISION_KINDS: dict[str, Any] = {
    CardsChosen.kind: lambda data: CardsChosen(tuple(data.get("cards") or ())),
    PlayerChosen.kind: lambda data: PlayerChosen(PlayerId(data["player"])),
    OptionChosen.kind: lambda data: OptionChosen(data["key"]),
    Confirmed.kind: lambda data: Confirmed(bool(data["ok"])),
    IntentChosen.kind: lambda data: IntentChosen(Intent.from_data(data["intent"])),
    ReactionChosen.kind: lambda data: ReactionChosen(data.get("card")),
}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Awaiting:
    """Suspended: somebody has to answer before the game moves."""

    request: Request


@dataclass(frozen=True, slots=True)
class Quiescent:
    """Between actions — the only point at which a save is legal."""

    outcome: Outcome = Outcome.DONE


@dataclass(frozen=True, slots=True)
class GameOver:
    winner: PlayerId | None


Status = Awaiting | Quiescent | GameOver

Flow = Generator[Request, Any, Any]


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------


class Interpreter:
    """Runs one effect flow at a time, suspending on every question.

    It holds no game rules whatsoever: it advances a generator, validates
    answers against the request that asked for them, and writes the log.
    """

    def __init__(self, state: GameState, *, log: DecisionLog | None = None) -> None:
        self.state = state
        self.log = log
        self._flow: Flow | None = None
        self._pending: Request | None = None
        self.outcome: Outcome = Outcome.DONE

    # -- state -------------------------------------------------------------

    @property
    def pending(self) -> Request | None:
        """The unanswered request, if the interpreter is suspended."""
        return self._pending

    @property
    def running(self) -> bool:
        return self._flow is not None

    # -- driving -----------------------------------------------------------

    def begin(self, flow: Flow) -> Status:
        """Start a new flow. Raises if one is already suspended — a half-asked
        question must be answered before anything else happens."""
        if self._flow is not None:
            raise EngineError(
                "a flow is already in progress; submit a decision before starting another"
            )
        self._flow = flow
        self.outcome = Outcome.DONE
        return self._advance(None)

    def submit(self, decision: Decision) -> Status:
        """Answer the pending request and resume."""
        if self._flow is None or self._pending is None:
            raise EngineError("nothing is pending; there is no question to answer")
        request = self._pending
        value = request.validate(decision)  # never trust the caller
        if self.log is not None:
            self.log.record(request, decision)
        self._pending = None
        return self._advance(value)

    def _advance(self, value: Any) -> Status:
        flow = self._flow
        assert flow is not None
        try:
            request = flow.send(value)
        except StopIteration as stop:
            self._flow = None
            self._pending = None
            self.outcome = stop.value if isinstance(stop.value, Outcome) else Outcome.DONE
            check_if_strict(self.state)
            return self._finished()
        if not isinstance(request, Request):
            self._flow = None
            raise EngineError(
                f"an effect yielded {type(request).__name__}, which is not a Request — "
                f"effects may only suspend by yielding ctx.ask_* results"
            )
        self._pending = request
        return Awaiting(request)

    def _finished(self) -> Status:
        if self.state.winner is not None:
            return GameOver(self.state.winner)
        return Quiescent(self.outcome)

    def abort(self) -> None:
        """Throw away a suspended flow (a replay mismatch, a torn-down test)."""
        if self._flow is not None:
            self._flow.close()
        self._flow = None
        self._pending = None


def drive(interpreter: Interpreter, flow: Flow, source: DecisionSource) -> Status:
    """Run ``flow`` to completion, taking every answer from ``source``.

    The whole driver loop, in one place: the CLI, the AI and the replay engine
    differ only in what they pass as ``source``.
    """
    status = interpreter.begin(flow)
    while isinstance(status, Awaiting):
        status = interpreter.submit(source.answer(status.request))
    return status


class DecisionSource:
    """Where answers come from. Implemented by presenters, agents and replays."""

    def answer(self, request: Request) -> Decision:  # pragma: no cover - interface
        raise NotImplementedError


class ScriptedSource(DecisionSource):
    """A fixed list of decisions — the test harness's presenter."""

    def __init__(self, decisions: Sequence[Decision]) -> None:
        self.decisions = list(decisions)
        self.index = 0
        self.seen: list[Request] = []

    def answer(self, request: Request) -> Decision:
        self.seen.append(request)
        if self.index >= len(self.decisions):
            raise EngineError(
                f"the script ran out of decisions at request {self.index + 1}: {request}"
            )
        decision = self.decisions[self.index]
        self.index += 1
        return decision

    @property
    def exhausted(self) -> bool:
        return self.index >= len(self.decisions)


__all__ = [
    "PASS",
    "Awaiting",
    "CardsChosen",
    "ChooseCards",
    "ChooseIntent",
    "ChooseOption",
    "ChoosePlayer",
    "Confirm",
    "Confirmed",
    "Decision",
    "DecisionSource",
    "Flow",
    "GameOver",
    "Intent",
    "IntentChosen",
    "Interpreter",
    "Option",
    "OptionChosen",
    "PlayerChosen",
    "Quiescent",
    "ReactionChosen",
    "ReactionPrompt",
    "Request",
    "ScriptedSource",
    "Status",
    "drive",
]
