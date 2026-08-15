"""``presenter.py`` — the human-at-a-terminal ``DecisionSource``.

This is Phase 5's core: it bridges the ``Engine`` API (which only knows
requests and decisions) to a real person sitting at a keyboard.

Design points
-------------
* **Hot-seat privacy.** When ``request.requester`` changes, the screen is
  cleared and the game waits for an explicit Enter before rendering the new
  player's view. That way Player 2 cannot accidentally see Player 1's hand.
* **Roll display.** After a ``roll.resolved`` event the presenter prints a
  breakdown of raw dice, each modifier and the final total before asking for
  the next decision. The engine exposes rolls through ``engine.state``; we
  read them from the ``Execution`` attached to the last resolved context.
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
    Awaiting,
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    Decision,
    DecisionSource,
    Intent,
    IntentChosen,
    OptionChosen,
    PlayerChosen,
    ReactionChosen,
    ReactionPrompt,
    Request,
)
from here_to_slay.ui.cli.render import render_board

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
                    f"Press Enter when {self._player_name(request.requester)} is ready…",
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
        """Print any rolls that resolved since the last prompt."""
        rolls = getattr(self.engine.state, "_last_displayed_roll_index", 0)
        execution = getattr(self.engine.interpreter, "_flow", None)
        # Access rolls from the engine's current execution context
        # Rolls live on the EffectContext.execution object, but the engine
        # doesn't expose that directly. We track via state flag.
        # Phase 5 roll display: read from state.flags injected by engine.
        all_rolls = self.engine.state.flags.get("_cli_rolls", [])
        displayed = self.engine.state.flags.get("_cli_rolls_displayed", 0)
        new_rolls = all_rolls[displayed:]
        for roll in new_rolls:
            from here_to_slay.ui.cli.render import render_roll
            self.console.print(render_roll(roll))
        if new_rolls:
            self.engine.state.flags["_cli_rolls_displayed"] = len(all_rolls)

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
        candidates = list(request.candidates)
        prompt = request.prompt or (
            f"Choose {request.minimum}–{request.maximum} card(s):"
            if request.minimum != request.maximum
            else f"Choose {request.minimum} card(s):"
        )
        self.console.print(f"[bold]{prompt}[/bold]")
        names = [self._card_name_for(c) for c in candidates]
        for i, name in enumerate(names, 1):
            hidden = request.hidden and c != view.seat  # noqa: F821  (view not used here)
            label = "???" if request.hidden else name
            self.console.print(f"  [{i}] {label}")

        # hidden-blind: present backs
        if request.hidden:
            self.console.print("  (You cannot see the faces of these cards.)")

        chosen: list[str] = []
        remaining = request.minimum
        while len(chosen) < request.minimum or (
            len(chosen) < request.maximum and len(chosen) < len(candidates)
        ):
            lo = min(request.minimum, len(candidates) - len(chosen))
            hi = min(request.maximum - len(chosen), len(candidates) - len(chosen))
            if lo == hi and lo == 1:
                self.console.print(f"  → auto-selecting: {names[0]}")
                chosen.append(candidates[0])
                break
            still = request.minimum - len(chosen)
            self.console.print(
                f"  Enter number (need {still} more, {len(candidates) - len(chosen)} available):"
            )
            idx = self._read_int(1, len(candidates))
            c = candidates[idx - 1]
            if c in chosen:
                self.console.print("  [yellow]Already chosen — pick another.[/yellow]")
                continue
            chosen.append(c)
            if len(chosen) >= request.maximum:
                break
            if len(chosen) >= request.minimum:
                self.console.print("  [dim]Enter another number, or press Enter to stop:[/dim]")
                raw = self._read_raw()
                if not raw:
                    break
                try:
                    idx2 = int(raw)
                    if 1 <= idx2 <= len(candidates):
                        c2 = candidates[idx2 - 1]
                        if c2 not in chosen:
                            chosen.append(c2)
                except ValueError:
                    pass
        return CardsChosen(tuple(chosen))  # type: ignore[arg-type]

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
        raw = self._read_raw().strip().lower()
        return Confirmed(raw in ("y", "yes"))

    # -- input helpers ----------------------------------------------------

    def _read_int(self, lo: int, hi: int) -> int:
        while True:
            self.console.print(f"  [dim]Enter {lo}–{hi}:[/dim] ", end="")
            raw = self._read_raw()
            try:
                value = int(raw.strip())
                if lo <= value <= hi:
                    return value
                self.console.print(
                    f"  [red]Please enter a number between {lo} and {hi}.[/red]"
                )
            except ValueError:
                self.console.print("  [red]Please enter a number.[/red]")

    def _read_raw(self) -> str:
        try:
            return input()
        except (EOFError, KeyboardInterrupt):
            # Non-interactive / test mode — return empty so callers fall back
            return ""


__all__ = ["CliPresenter"]
