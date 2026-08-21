"""``hts`` — the command line entry point.

Phase 1 ships ``hts validate``. Phase 5 adds ``hts play`` and ``hts replay``.
``hts gui`` arrives with Phase 9 and is deliberately *not* declared yet, so
``--help`` never advertises a command that does not run.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

from here_to_slay import __version__
from here_to_slay.content.errors import ContentError, ContentIssue, Severity
from here_to_slay.content.loader import load_packs
from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.validate import validate_registry

EXIT_OK = 0
EXIT_CONTENT_ERROR = 1
EXIT_USAGE = 2


def _random_seed() -> str:
    import secrets

    return secrets.token_hex(6)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hts",
        description="Here to Slay - a moddable, data-driven card-game engine.",
    )
    parser.add_argument("--version", action="version", version=f"hts {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    validate = subparsers.add_parser(
        "validate",
        help="check a content pack without launching a game",
        description=(
            "Load one or more content packs, then run the structural (pydantic) and "
            "semantic passes. Exits non-zero on error, so this is CI-able."
        ),
    )
    validate.add_argument("packs", nargs="+", metavar="PACK", help="pack directory or pack.yaml")
    validate.add_argument(
        "--search-path",
        action="append",
        default=[],
        metavar="DIR",
        help="extra directory to search for required packs (repeatable)",
    )
    validate.add_argument("--strict", action="store_true", help="treat warnings as errors")
    validate.add_argument(
        "--no-art-check", action="store_true", help="skip the 'art file exists' warning"
    )
    validate.add_argument("-q", "--quiet", action="store_true", help="only print the summary")
    validate.set_defaults(func=cmd_validate)

    # ------------------------------------------------------------------
    # play
    # ------------------------------------------------------------------
    play = subparsers.add_parser(
        "play",
        help="start a hot-seat game in the terminal",
        description=(
            "Deal a game and play it in the terminal. All players share the same "
            "keyboard; the screen is cleared between turns for privacy."
        ),
    )
    play.add_argument(
        "packs",
        nargs="*",
        metavar="PACK",
        default=["data/base"],
        help="content packs to load (default: data/base)",
    )
    play.add_argument(
        "--search-path",
        action="append",
        default=[],
        metavar="DIR",
        help="extra directory to search for required packs (repeatable)",
    )
    play.add_argument(
        "--names",
        nargs="+",
        metavar="NAME",
        default=[],
        help="player names (default: Player 1, Player 2, …)",
    )
    play.add_argument(
        "--players",
        type=int,
        default=2,
        metavar="N",
        help="number of players (default: 2, ignored if --names is given)",
    )
    play.add_argument(
        "--seed",
        default=None,
        metavar="SEED",
        help="RNG seed for a reproducible game",
    )
    play.add_argument(
        "--max-turns",
        type=int,
        default=0,
        metavar="N",
        help="stop after N turns (0 = no limit)",
    )
    play.add_argument(
        "--no-save",
        action="store_true",
        help="do not save the decision log at the end",
    )
    play.set_defaults(func=cmd_play)

    # ------------------------------------------------------------------
    # replay
    # ------------------------------------------------------------------
    replay = subparsers.add_parser(
        "replay",
        help="replay a saved game log",
        description=(
            "Load a decision log (saved by 'hts play') and replay it, printing "
            "the board after every decision."
        ),
    )
    replay.add_argument("log", metavar="LOG_FILE", help="path to the .json decision log")
    replay.add_argument(
        "packs",
        nargs="*",
        metavar="PACK",
        default=["data/base"],
        help="content packs to load (default: data/base)",
    )
    replay.add_argument(
        "--search-path",
        action="append",
        default=[],
        metavar="DIR",
        help="extra directory to search for required packs (repeatable)",
    )
    replay.add_argument(
        "--step",
        action="store_true",
        help="pause after each decision (press Enter to advance)",
    )
    replay.set_defaults(func=cmd_replay)

    return parser


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace, console: Console) -> int:
    try:
        registry = load_packs(args.packs, search_paths=args.search_path)
    except ContentError as exc:
        _report(console, exc.issues, quiet=args.quiet)
        console.print(_summary_line(exc.issues, loaded=False))
        return EXIT_CONTENT_ERROR

    issues = validate_registry(registry, check_art=not args.no_art_check)
    _report(console, issues, quiet=args.quiet)

    errors = [issue for issue in issues if issue.is_error]
    warnings = [issue for issue in issues if not issue.is_error]
    console.print(_summary_line(issues, loaded=True, registry=registry))

    if errors or (args.strict and warnings):
        return EXIT_CONTENT_ERROR
    return EXIT_OK


def _report(console: Console, issues: Sequence[ContentIssue], *, quiet: bool = False) -> None:
    if not issues or quiet:
        return
    table = Table(show_header=True, header_style="bold", highlight=False)
    table.add_column("", width=1)
    table.add_column("where", style="cyan", overflow="fold")
    table.add_column("problem", overflow="fold")

    for issue in sorted(issues, key=lambda i: (not i.is_error, i.path)):
        marker = Text("x", style="bold red") if issue.is_error else Text("!", style="yellow")
        problem = Text(issue.message)
        if issue.hint:
            problem.append(f"\n{issue.hint}", style="dim")
        table.add_row(marker, issue.path, problem)

    console.print(table)


def _summary_line(
    issues: Sequence[ContentIssue],
    *,
    loaded: bool,
    registry: ContentRegistry | None = None,
) -> Text:
    errors = sum(1 for i in issues if i.severity is Severity.ERROR)
    warnings = len(issues) - errors

    if errors:
        line = Text(f"FAILED - {errors} error(s), {warnings} warning(s)", style="bold red")
        if not loaded:
            line.append("  (pack could not be loaded)", style="dim")
        return line

    line = Text("OK", style="bold green")
    if registry is not None:
        deck = registry.deck_composition()
        line.append(
            f" - {len(registry.cards)} card definitions"
            f", {sum(deck.values())} physical cards"
            f", {len(registry.packs)} pack(s)",
            style="default",
        )
        line.append(f"\ncontent hash {registry.content_hash[:12]}", style="dim")
    if warnings:
        line.append(f"\n{warnings} warning(s)", style="yellow")
    return line


# ---------------------------------------------------------------------------
# play
# ---------------------------------------------------------------------------


def cmd_play(args: argparse.Namespace, console: Console) -> int:
    import datetime
    import re
    from pathlib import Path

    # -- load content ------------------------------------------------------
    try:
        registry = load_packs(args.packs, search_paths=args.search_path)
    except ContentError as exc:
        console.print(f"[bold red]Content error:[/bold red] {exc}")
        return EXIT_CONTENT_ERROR

    # -- player names ------------------------------------------------------
    names: list[str]
    if args.names:
        names = list(args.names)
    else:
        n = max(2, args.players)
        names = [f"Player {i}" for i in range(1, n + 1)]

    n_players = len(names)
    max_p = registry.rules.setup.max_players
    min_p = registry.rules.setup.min_players
    if not (min_p <= n_players <= max_p):
        console.print(
            f"[red]This rule set requires {min_p}–{max_p} players; "
            f"got {n_players}.[/red]"
        )
        return EXIT_USAGE

    # -- seed --------------------------------------------------------------
    seed: int | str = args.seed if args.seed is not None else _random_seed()

    # -- build engine ------------------------------------------------------
    from here_to_slay.core.engine import Engine

    engine = Engine.new(registry, names, seed=seed, max_turns=args.max_turns)

    console.print()
    console.print(
        f"[bold bright_green]Here to Slay[/bold bright_green]  "
        f"[dim]seed={seed!r}  {n_players} players[/dim]"
    )
    console.print()

    # -- run ---------------------------------------------------------------
    from here_to_slay.core.interpreter import GameOver
    from here_to_slay.ui.cli.presenter import CliPresenter
    from here_to_slay.ui.cli.render import ICONS

    TROPHY = ICONS["trophy"]

    presenter = CliPresenter(engine, registry, console=console)
    try:
        status = engine.run(presenter)
    except KeyboardInterrupt:
        console.print("\n[yellow]Game interrupted.[/yellow]")
        return EXIT_OK

    # -- result ------------------------------------------------------------
    if isinstance(status, GameOver) and status.winner:
        winner_name = engine.state.player(status.winner).name
        console.print(f"\n[bold bright_yellow]{TROPHY} {winner_name} wins![/bold bright_yellow]\n")
    else:
        console.print("\n[dim]Game over (no winner / turn cap reached).[/dim]\n")

    # -- save log ----------------------------------------------------------
    if not args.no_save:
        log_dir = Path.cwd() / "hts_logs"
        log_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_seed = re.sub(r"[^\w\-]", "_", str(seed))
        log_path = log_dir / f"{ts}_{safe_seed}.json"
        engine.log.save(log_path)
        console.print(f"[dim]Decision log saved → {log_path}[/dim]")

    return EXIT_OK


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def cmd_replay(args: argparse.Namespace, console: Console) -> int:
    from pathlib import Path

    from here_to_slay.core.engine import Engine
    from here_to_slay.core.errors import ReplayError, ReplayExhausted
    from here_to_slay.core.interpreter import GameOver
    from here_to_slay.core.log import DecisionLog, LogSource

    # -- load log ----------------------------------------------------------
    log_path = Path(args.log)
    if not log_path.exists():
        console.print(f"[red]Log file not found: {log_path}[/red]")
        return EXIT_USAGE
    try:
        log = DecisionLog.load(log_path)
    except Exception as exc:
        console.print(f"[red]Could not load log: {exc}[/red]")
        return EXIT_USAGE

    # -- load content ------------------------------------------------------
    try:
        registry = load_packs(args.packs, search_paths=args.search_path)
    except ContentError as exc:
        console.print(f"[bold red]Content error:[/bold red] {exc}")
        return EXIT_CONTENT_ERROR

    # -- build engine ------------------------------------------------------
    try:
        engine, log_source = Engine.replaying(registry, log)
    except ReplayError as exc:
        console.print(f"[bold red]Replay error:[/bold red] {exc}")
        return EXIT_USAGE

    console.print()
    console.print(
        f"[bold bright_cyan]Replaying[/bold bright_cyan]  "
        f"[dim]seed={log.seed!r}  {len(log.players)} players  "
        f"{len(log.entries)} decision(s)[/dim]"
    )
    console.print()

    # -- run (with per-step rendering) ------------------------------------
    from here_to_slay.core.interpreter import Decision, DecisionSource, Request
    from here_to_slay.ui.cli.render import ICONS, render_board

    TROPHY = ICONS["trophy"]

    def _step_pause() -> None:
        if args.step:
            console.print("[dim]Press Enter to continue…[/dim]")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass

    class ReplayViewer(DecisionSource):
        """Renders the board before every logged decision, then answers from it.

        There is no "have we run out?" check here any more: ``LogSource`` raises
        :class:`ReplayExhausted` when the log ends, which is the honest place to
        detect it. The board is drawn before the call either way, so a partial
        log still shows the position it stopped at.
        """

        def __init__(self, source: LogSource) -> None:
            self.source = source

        def answer(self, request: Request) -> Decision:
            view = engine.view(request.requester)
            console.print(render_board(view, registry))
            console.print()
            _step_pause()
            return self.source.answer(request)

    viewer = ReplayViewer(log_source)
    status: object = None
    try:
        status = engine.run(viewer)
    except ReplayExhausted:
        # The expected end of a partial log — a distinct type rather than a
        # substring of somebody else's message (Phase 5 gap 6).
        pass
    except KeyboardInterrupt:
        console.print("\n[yellow]Replay interrupted.[/yellow]")
        return EXIT_OK
    except ReplayError as exc:
        console.print(f"[bold red]Replay error:[/bold red] {exc}")
        return EXIT_USAGE

    # -- final board -------------------------------------------------------
    active_seat = engine.state.active_player
    final_view = engine.view(active_seat)
    console.print(render_board(final_view, registry))
    console.print()

    if isinstance(status, GameOver) and status.winner:
        winner_name = engine.state.player(status.winner).name
        console.print(f"[bold bright_yellow]{TROPHY} {winner_name} wins![/bold bright_yellow]")
    else:
        console.print("[dim]Replay finished.[/dim]")
    return EXIT_OK


def _make_console() -> Console:
    """A console that cannot die of an unencodable character.

    A legacy Windows console runs a non-UTF-8 code page, where printing a box
    glyph or an emoji raises ``UnicodeEncodeError`` and takes the game down
    mid-board. ``errors="replace"`` degrades those to '?' instead; the ASCII
    icon fallback in ``ui/cli/render.py`` keeps the *common* glyphs readable, so
    in practice only rare decoration is affected.
    """
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError):  # pragma: no cover - exotic stdout
        pass
    return Console()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    console = _make_console()
    return int(args.func(args, console))


if __name__ == "__main__":
    sys.exit(main())
