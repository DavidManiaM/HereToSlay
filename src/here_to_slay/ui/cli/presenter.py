"""``presenter.py`` — the human-at-a-terminal ``DecisionSource``.

This is Phase 5's core: it bridges the ``Engine`` API (which only knows
requests and decisions) to a real person sitting at a keyboard.

Design points
-------------
* **Hot-seat privacy.** When ``request.requester`` changes, the screen is
  cleared and the game waits for an explicit Enter before rendering the new
  player's view. That way Player 2 cannot accidentally see Player 1's hand.
* **Roll display.** Before every prompt the presenter prints any roll made
  since it last asked: raw dice, each modifier with its source, the total, and
  the outcome band's tag once one has been chosen. It reads
  ``engine.recent_rolls`` and keeps its own "already shown" count, so the UI
  needs neither the bus nor ``GameState``.
* **Numbered menus.** Every prompt is ``[1] label``, ``[2] label``, …,  then
  ``Enter a number: ``. Invalid input re-prompts until the player enters a
  valid choice.
* **Replay mode.** ``CliPresenter`` accepts an optional ``silent=True`` flag.
  In silent mode it does not clear the screen and does not prompt; it just
  renders after each decision. Used by ``hts replay``.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from here_to_slay.core.interpreter import (
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    Decision,
    DecisionSource,
    IntentChosen,
    OptionChosen,
    PlayerChosen,
    ReactionChosen,
    ReactionPrompt,
    Request,
)
from here_to_slay.ui.cli.render import render_board, render_roll, render_roll_result

if TYPE_CHECKING:
    from here_to_slay.content.registry import ContentRegistry
    from here_to_slay.core.engine import Engine


class CliPresenter(DecisionSource):
    """A human player at a terminal.

    Parameters
    ----------
    engine:
        The running game. The presenter reads ``engine.view(seat)`` before
        each prompt and ``engine.state`` for roll history.
    registry:
        Content registry, used by the renderer to resolve card names.
    console:
        The ``rich`` console to write to. Defaults to stdout.
    silent:
        If ``True``, never clear the screen, never block for Enter on seat
        change. Used by the replay driver.
    """

    def __init__(
        self,
        engine: Engine,
        registry: ContentRegistry,
        *,
        console: Console | None = None,
        silent: bool = False,
    ) -> None:
        self.engine = engine
        self.registry = registry
        self.console = console or Console()
        self.silent = silent
        self._last_seat: str | None = None
        #: how many of ``engine.recent_rolls`` this terminal has already drawn
        self._rolls_shown = 0

    # -- DecisionSource implementation ------------------------------------

    def answer(self, request: Request) -> Decision:
        """Render the board, print the prompt, read a valid decision."""
        self._maybe_seat_change(request)
        view = self.engine.view(request.requester)
        self._render(view)
        self._print_last_rolls()
        return self._prompt(request, view)

    # -- seat change gate -------------------------------------------------

    def _maybe_seat_change(self, request: Request) -> None:
        if self.silent:
            return
        if request.requester == self._last_seat:
            return
        # Different player — privacy gate
        if self._last_seat is not None:
            self._clear()
            self.console.print(
                Rule(
                    f"[bold bright_yellow]Passing to {self._player_name(request.requester)}[/bold bright_yellow]",
                    style="yellow",
                )
            )
            self.console.print(
                Text(
                    f"Press Enter when {self._player_name(request.requester)} is ready...",
                    style="dim",
                )
            )
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        self._last_seat = request.requester

    def _player_name(self, seat: str) -> str:
        try:
            return self.engine.state.player(seat).name  # type: ignore[arg-type]
        except Exception:
            return str(seat)

    # -- rendering --------------------------------------------------------

    def _clear(self) -> None:
        if self.silent:
            return
        if sys.stdout.isatty():
            os.system("cls" if os.name == "nt" else "clear")

    def _render(self, view: Any) -> None:
        board = render_board(view, self.registry)
        self.console.print()
        self.console.print(board)
        self.console.print()

    def _print_last_rolls(self) -> None:
        """Print every roll made since this presenter last prompted.

        Reads ``engine.recent_rolls`` — a plain accessor, so the UI stays off
        the event bus and off ``GameState``. The count of what has been shown
        lives on the presenter, not in game flags: how much of the board a
        particular terminal has already drawn is not part of the game.

        A roll whose outcome band has been chosen is printed with the band's
        tag (``→ success``); one still in flight — which is exactly what a
        player being asked to modify a Challenge is looking at — is printed
        without, because no band has run yet.
        """
        rolls = self.engine.recent_rolls
        for roll in rolls[self._rolls_shown :]:
            if roll.band_tag:
                self.console.print(render_roll_result(roll, roll.band_tag))
            else:
                self.console.print(render_roll(roll))
        self._rolls_shown = len(rolls)

    # -- prompts ----------------------------------------------------------

    def _prompt(self, request: Request, view: Any) -> Decision:
        match request.kind:
            case "choose_intent":
                return self._choose_intent(request)  # type: ignore[arg-type]
            case "choose_cards":
                return self._choose_cards(request, view)  # type: ignore[arg-type]
            case "choose_player":
                return self._choose_player(request, view)
            case "choose_option":
                return self._choose_option(request)  # type: ignore[arg-type]
            case "reaction":
                return self._choose_reaction(request)  # type: ignore[arg-type]
            case "confirm":
                return self._confirm(request)
            case _:
                # Unknown request kind — just skip by returning an empty decision
                # A plugin might add new kinds; we re-raise so they surface.
                raise NotImplementedError(
                    f"CliPresenter does not handle request kind '{request.kind}'"
                )

    # ---- intent menu ----------------------------------------------------

    def _choose_intent(self, request: ChooseIntent) -> IntentChosen:
        intents = list(request.intents)
        prompt = request.prompt or "What do you do?"
        self.console.print(f"[bold]{prompt}[/bold]")
        for i, intent in enumerate(intents, 1):
            self.console.print(f"  [{i}] {intent.label or intent.key()}")
        choice = self._read_int(1, len(intents))
        return IntentChosen(intents[choice - 1])

    # ---- cards ----------------------------------------------------------

    def _choose_cards(self, request: ChooseCards, view: Any) -> CardsChosen:
        """Pick between ``minimum`` and ``maximum`` cards, one number at a time.

        The loop only ever offers cards not already taken, which is what keeps
        it honest: the engine rejects a selection containing the same card
        twice, so offering one is offering an illegal move. A blind pick
        (``request.hidden`` — choosing out of a hand you cannot see) shows
        positions rather than names.
        """
        candidates = list(request.candidates)
        prompt = request.prompt or (
            f"Choose {request.minimum}-{request.maximum} card(s):"
            if request.minimum != request.maximum
            else f"Choose {request.minimum} card(s):"
        )
        self.console.print(f"[bold]{prompt}[/bold]")

        # A blind pick names positions, never faces: the whole point is that the
        # chooser cannot see this zone.
        names = [
            f"card {i}" if request.hidden else self._card_name_for(card)
            for i, card in enumerate(candidates, 1)
        ]
        for i, name in enumerate(names, 1):
            self.console.print(f"  [{i}] {name}")
        if request.hidden:
            self.console.print("  [dim](You cannot see the faces of these cards.)[/dim]")

        chosen: list[str] = []
        maximum = min(request.maximum, len(candidates))
        minimum = min(request.minimum, maximum)

        while len(chosen) < maximum:
            available = [c for c in candidates if c not in chosen]
            if not available:
                break
            still = minimum - len(chosen)
            # Forced and unambiguous: take it rather than asking a question with
            # exactly one legal answer.
            if len(available) == 1 and still > 0:
                only = available[0]
                self.console.print(
                    f"  [dim]-> {names[candidates.index(only)]} (only choice)[/dim]"
                )
                chosen.append(only)
                continue

            if still > 0:
                self.console.print(f"  [dim](need {still} more)[/dim]")
            else:
                self.console.print("  [dim]Enter another number, or press Enter to stop:[/dim]")
                raw = self._read_raw()
                if not raw:  # Enter, or a closed stream: either way, we are done
                    break
                pick = self._parse_pick(raw, candidates, chosen)
                if pick is None:
                    continue
                chosen.append(pick)
                continue

            index = self._read_int(1, len(candidates))
            card = candidates[index - 1]
            if card in chosen:
                self.console.print("  [yellow]Already chosen - pick another.[/yellow]")
                continue
            chosen.append(card)

        return CardsChosen(tuple(chosen))  # type: ignore[arg-type]

    def _parse_pick(
        self, raw: str, candidates: list[str], chosen: list[str]
    ) -> str | None:
        """A 1-based index typed at the optional-extra prompt, or ``None``."""
        try:
            index = int(raw.strip())
        except ValueError:
            return None
        if not 1 <= index <= len(candidates):
            return None
        card = candidates[index - 1]
        return None if card in chosen else card

    def _card_name_for(self, card_id: str) -> str:
        try:
            return self.engine.state.definition(card_id).name  # type: ignore[arg-type]
        except Exception:
            return str(card_id)

    # ---- player ---------------------------------------------------------

    def _choose_player(self, request: ChoosePlayer, view: Any) -> PlayerChosen:
        candidates = list(request.candidates)
        prompt = request.prompt or "Choose a player:"
        self.console.print(f"[bold]{prompt}[/bold]")
        for i, pid in enumerate(candidates, 1):
            name = self._player_name(pid)
            self.console.print(f"  [{i}] {name}")
        choice = self._read_int(1, len(candidates))
        return PlayerChosen(candidates[choice - 1])

    # ---- option ---------------------------------------------------------

    def _choose_option(self, request: ChooseOption) -> OptionChosen:
        options = list(request.options)
        prompt = request.prompt or "Choose an option:"
        self.console.print(f"[bold]{prompt}[/bold]")
        for i, opt in enumerate(options, 1):
            self.console.print(f"  [{i}] {opt.label}")
        choice = self._read_int(1, len(options))
        return OptionChosen(options[choice - 1].key)

    # ---- reaction -------------------------------------------------------

    def _choose_reaction(self, request: ReactionPrompt) -> ReactionChosen:
        options = list(request.options)
        prompt = request.prompt or f"React to '{request.window}'?"
        self.console.print(f"[bold yellow]{prompt}[/bold yellow]")
        for i, opt in enumerate(options, 1):
            name = opt.label or self._card_name_for(opt.card or "")
            self.console.print(f"  [{i}] Play: {name}")
        self.console.print(f"  [{len(options) + 1}] Pass")
        choice = self._read_int(1, len(options) + 1)
        if choice == len(options) + 1:
            return ReactionChosen(None)
        return ReactionChosen(options[choice - 1].card)

    # ---- confirm --------------------------------------------------------

    def _confirm(self, request: Confirm) -> Confirmed:
        prompt = request.prompt or "Confirm?"
        self.console.print(f"[bold]{prompt}[/bold] [dim][y/N][/dim] ", end="")
        raw = (self._read_raw() or "").strip().lower()
        return Confirmed(raw in ("y", "yes"))

    # -- input helpers ----------------------------------------------------

    def _read_int(self, lo: int, hi: int) -> int:
        """Prompt until a number in range arrives, or the input stream ends.

        The EOF check is what stops a piped session spinning forever: a closed
        stdin returns '' from every subsequent read, and re-prompting on that is
        an infinite loop rather than a retry. A human pressing Enter by mistake
        still just gets asked again, because ``_stdin_open`` is still true.
        """
        while True:
            self.console.print(f"  [dim]Enter {lo}-{hi}:[/dim] ", end="")
            raw = self._read_raw()
            if raw is None:
                raise EOFError(
                    f"stdin closed while waiting for a number between {lo} and {hi}"
                )
            try:
                value = int(raw.strip())
                if lo <= value <= hi:
                    return value
                self.console.print(
                    f"  [red]Please enter a number between {lo} and {hi}.[/red]"
                )
            except ValueError:
                self.console.print("  [red]Please enter a number.[/red]")

    def _read_raw(self) -> str | None:
        """A line of input, or ``None`` once the stream is exhausted.

        ``None`` and ``''`` mean different things — "there will never be more
        input" versus "the player pressed Enter" — and collapsing them is what
        made a closed stdin loop forever.
        """
        try:
            return input()
        except (EOFError, KeyboardInterrupt):
            return None


__all__ = ["CliPresenter"]
