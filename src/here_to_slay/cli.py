"""``hts`` — the command line entry point.

``validate`` checks a content pack, ``play`` runs the terminal client, ``gui``
opens the pygame client, ``replay`` re-runs a saved decision log, and ``sim``
fuzzes headless games. Every command loads content the same way, so a variant
pack works everywhere the base game does.
"""

from __future__ import annotations

import argparse
import contextlib
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
from here_to_slay.content.vocabulary import Vocabulary
from here_to_slay.modding import load_plugins

EXIT_OK = 0
EXIT_CONTENT_ERROR = 1
EXIT_USAGE = 2
EXIT_RUNTIME_ERROR = 3


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
    # gui
    # ------------------------------------------------------------------
    gui = subparsers.add_parser(
        "gui",
        help="start a graphical PyGame client",
        description="Launch the PyGame desktop client for Here to Slay.",
    )
    gui.add_argument(
        "packs",
        nargs="*",
        metavar="PACK",
        default=["data/base"],
        help="content packs to load (default: data/base)",
    )
    gui.add_argument(
        "--search-path",
        action="append",
        default=[],
        metavar="DIR",
        help="extra directory to search for required packs (repeatable)",
    )
    gui.add_argument(
        "--names",
        nargs="+",
        metavar="NAME",
        default=[],
        help="player names (default: Player 1, Player 2, …)",
    )
    gui.add_argument(
        "--players",
        type=int,
        default=2,
        metavar="N",
        help="number of players (default: 2, ignored if --names is given)",
    )
    gui.add_argument(
        "--seed",
        default=None,
        metavar="SEED",
        help="RNG seed for a reproducible game",
    )
    gui.add_argument(
        "--max-turns",
        type=int,
        default=0,
        metavar="N",
        help="stop after N turns (0 = no limit)",
    )
    gui.add_argument(
        "--ai",
        type=int,
        default=0,
        metavar="N",
        help="let the agent play the last N seats (default: 0 — pass the mouse round)",
    )
    gui.add_argument(
        "--width",
        type=int,
        default=1920,
        metavar="W",
        help="window width (default: 1920, clamped to the desktop)",
    )
    gui.add_argument(
        "--height",
        type=int,
        default=1080,
        metavar="H",
        help="window height (default: 1080, clamped to the desktop)",
    )
    gui.add_argument(
        "--ui-scale",
        type=float,
        default=1.0,
        metavar="F",
        help="chrome scale — below 1.0 shrinks the HUD so the board grows "
             "(default: 1.0)",
    )
    gui.add_argument(
        "--fullscreen", action="store_true", help="start fullscreen (F11 toggles)"
    )
    gui.add_argument(
        "--no-sound", action="store_true", help="start with the procedural cues muted"
    )
    gui.add_argument(
        "--reveal-all",
        action="store_true",
        help="spectator mode: show every hand (for demos and debugging)",
    )
    gui.set_defaults(func=cmd_gui)

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

    # ------------------------------------------------------------------
    # sim
    # ------------------------------------------------------------------
    sim = subparsers.add_parser(
        "sim",
        help="run headless games to fuzz for bugs and invariant violations",
        description=(
            "Simulate multiple games headlessly with AI agents to test stability, "
            "balance, termination, and state invariants."
        ),
    )
    sim.add_argument(
        "packs",
        nargs="*",
        metavar="PACK",
        default=["data/base"],
        help="content packs to load (default: data/base)",
    )
    sim.add_argument(
        "--search-path",
        action="append",
        default=[],
        metavar="DIR",
        help="extra directory to search for required packs (repeatable)",
    )
    sim.add_argument(
        "--games",
        type=int,
        default=1000,
        metavar="N",
        help="number of games to simulate (default: 1000)",
    )
    sim.add_argument(
        "--players",
        type=int,
        default=3,
        metavar="N",
        help="number of players per game (default: 3)",
    )
    sim.add_argument(
        "--agent",
        choices=["random", "heuristic"],
        default="random",
        help="agent type to use: random or heuristic (default: random)",
    )
    sim.add_argument(
        "--seed-start",
        type=int,
        default=0,
        metavar="SEED",
        help="starting seed integer (default: 0)",
    )
    sim.add_argument(
        "--max-turns",
        type=int,
        default=60,
        metavar="N",
        help="maximum turns per game (default: 60)",
    )
    sim.add_argument(
        "--ai-weights",
        default=None,
        metavar="PATH",
        help="path to custom AI weights YAML (heuristic agent only)",
    )
    sim.add_argument(
        "--strict",
        action="store_true",
        help="enable continuous invariant checks after every mutation",
    )
    sim.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="suppress progress bar and per-game logs",
    )
    sim.set_defaults(func=cmd_sim)

    # ------------------------------------------------------------------
    # new-pack
    # ------------------------------------------------------------------
    new_pack_cmd = subparsers.add_parser(
        "new-pack",
        help="scaffold a variant pack you can edit and play immediately",
        description=(
            "Write a runnable skeleton pack: a manifest, a rules overlay, one card, "
            "and optionally a plugin.py. The result validates and plays as soon as "
            "it is created, so the first edit is a change rather than a guess."
        ),
    )
    new_pack_cmd.add_argument("name", metavar="NAME", help="pack id (lower_snake_case)")
    new_pack_cmd.add_argument(
        "--dir",
        dest="directory",
        default="data/variants",
        metavar="DIR",
        help="where to create it (default: data/variants)",
    )
    new_pack_cmd.add_argument(
        "--requires",
        nargs="*",
        default=["base"],
        metavar="PACK",
        help="pack ids this one builds on (default: base; pass none for standalone)",
    )
    new_pack_cmd.add_argument(
        "--plugin",
        action="store_true",
        help="also write a plugin.py with an example op of each kind",
    )
    new_pack_cmd.add_argument(
        "--force", action="store_true", help="write into a directory that is not empty"
    )
    new_pack_cmd.set_defaults(func=cmd_new_pack)

    # ------------------------------------------------------------------
    # diff-pack
    # ------------------------------------------------------------------
    diff = subparsers.add_parser(
        "diff-pack",
        help="show what one pack changes about another",
        description=(
            "Load both sides and compare the resolved content — the tables the engine "
            "really walks — so a deep-merged variant shows up as a diff instead of a "
            "file you have to read next to the original."
        ),
    )
    diff.add_argument("base", metavar="BASE", help="the pack being changed")
    diff.add_argument("variant", metavar="VARIANT", help="the pack doing the changing")
    diff.add_argument(
        "--search-path",
        action="append",
        default=[],
        metavar="DIR",
        help="extra directory to search for required packs (repeatable)",
    )
    diff.add_argument(
        "--cards", action="store_true", help="also list every changed field of every card"
    )
    diff.set_defaults(func=cmd_diff_pack)

    return parser


# ---------------------------------------------------------------------------
# Loading content, the same way for every command
# ---------------------------------------------------------------------------


def load_content(args: argparse.Namespace) -> tuple[ContentRegistry, Vocabulary]:
    """Load the requested packs *and* import their plugins.

    Every command goes through here, which is what makes a variant with a
    ``plugin.py`` work everywhere the base game does: ``play``, ``gui``, ``sim``
    and ``replay`` need the ops registered before the first effect runs, and
    ``validate`` needs the vocabulary those ops imply before it can tell a new
    verb from a typo. Doing it in one place is why adding a command later cannot
    forget to.
    """
    registry = load_packs(args.packs, search_paths=args.search_path)
    return registry, load_plugins(registry)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace, console: Console) -> int:
    try:
        registry, vocabulary = load_content(args)
    except ContentError as exc:
        _report(console, exc.issues, quiet=args.quiet)
        console.print(_summary_line(exc.issues, loaded=False))
        return EXIT_CONTENT_ERROR

    issues = validate_registry(
        registry, vocabulary=vocabulary, check_art=not args.no_art_check
    )
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
        registry, _ = load_content(args)
    except ContentError as exc:
        console.print(f"[bold red]Content error:[/bold red] {exc}")
        return EXIT_CONTENT_ERROR

    # -- player names ------------------------------------------------------
    names: list[str]
    if args.names:
        names = list(args.names)
    else:
        n = max(2, args.players)
        names = [f"Jucător {i}" for i in range(1, n + 1)]

    n_players = len(names)
    max_p = registry.rules.setup.max_players
    min_p = registry.rules.setup.min_players
    if not (min_p <= n_players <= max_p):
        console.print(
            f"[red]This rule set requires {min_p}-{max_p} players; "
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
# gui
# ---------------------------------------------------------------------------


def cmd_gui(args: argparse.Namespace, console: Console) -> int:
    try:
        registry, _ = load_content(args)
    except ContentError as exc:
        console.print(f"[bold red]Content error:[/bold red] {exc}")
        return EXIT_CONTENT_ERROR

    names: list[str]
    if args.names:
        names = list(args.names)
    else:
        n = max(2, args.players)
        names = [f"Jucător {i}" for i in range(1, n + 1)]

    n_players = len(names)
    max_p = registry.rules.setup.max_players
    min_p = registry.rules.setup.min_players
    if not (min_p <= n_players <= max_p):
        console.print(
            f"[red]This rule set requires {min_p}-{max_p} players; "
            f"got {n_players}.[/red]"
        )
        return EXIT_USAGE

    ai_seats = max(0, min(args.ai, n_players - 1))
    if args.ai > ai_seats:
        console.print(
            f"[yellow]Only {ai_seats} of {n_players} seats can be AI — "
            f"somebody has to hold the mouse.[/yellow]"
        )

    seed: int | str = args.seed if args.seed is not None else _random_seed()

    from here_to_slay.ui.pygame import launch

    console.print(
        f"[bold bright_green]Here to Slay[/bold bright_green]  "
        f"[dim]seed={seed!r}  {n_players} players"
        f"{f', {ai_seats} AI' if ai_seats else ''}[/dim]"
    )
    console.print("[dim]I = rules  ·  L = log  ·  Esc = menu  ·  Ctrl+Shift+D = dev console[/dim]")

    code = launch(
        registry,
        names,
        seed=seed,
        max_turns=args.max_turns,
        ai_seats=ai_seats,
        width=args.width,
        height=args.height,
        ui_scale=args.ui_scale,
        fullscreen=args.fullscreen,
        reveal_all=args.reveal_all,
        sound=not args.no_sound,
    )
    return EXIT_OK if code == 0 else EXIT_RUNTIME_ERROR


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
        registry, _ = load_content(args)
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
            with contextlib.suppress(EOFError, KeyboardInterrupt):
                input()

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


# ---------------------------------------------------------------------------
# sim
# ---------------------------------------------------------------------------


def cmd_sim(args: argparse.Namespace, console: Console) -> int:
    import os
    import time

    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    if args.strict:
        os.environ["HTS_STRICT"] = "1"

    # -- load content ------------------------------------------------------
    try:
        registry, _ = load_content(args)
    except ContentError as exc:
        console.print(f"[bold red]Content error:[/bold red] {exc}")
        return EXIT_CONTENT_ERROR

    from here_to_slay.ai.heuristic_agent import HeuristicAgent
    from here_to_slay.ai.random_agent import RandomAgent
    from here_to_slay.core.engine import Engine
    from here_to_slay.core.invariants import find_violations
    from here_to_slay.core.victory import satisfied_by

    n_players = max(2, args.players)
    players = [f"Jucător {i}" for i in range(1, n_players + 1)]

    # Locate default weights if using heuristic and none specified
    weights_path = args.ai_weights
    if args.agent == "heuristic" and weights_path is None:
        # Check standard location in pack roots
        for root in registry.roots:
            candidate = root / "ai_weights.yaml"
            if candidate.exists():
                weights_path = candidate
                break

    console.print()
    console.print(
        f"[bold bright_cyan]Simulation[/bold bright_cyan]  "
        f"[dim]{args.games} games · {n_players} players · agent: {args.agent} "
        f"· max_turns: {args.max_turns} · strict: {args.strict}[/dim]"
    )
    console.print()

    # -- run simulation ----------------------------------------------------
    wins_by_condition: dict[str, int] = {}
    wins_by_seat: dict[str, int] = {}
    timeouts = 0
    errors = 0
    invariant_violations = 0
    total_turns = 0
    first_error_msg: str | None = None

    start_time = time.perf_counter()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        disable=args.quiet,
    )

    with progress:
        task_id = progress.add_task("Simulating games...", total=args.games)
        for i in range(args.games):
            seed = args.seed_start + i
            try:
                engine = Engine.new(
                    registry,
                    players,
                    seed=seed,
                    max_turns=args.max_turns,
                )
                if args.agent == "random":
                    agent = RandomAgent(seed=seed)
                else:
                    agent = HeuristicAgent(seed=seed, weights_path=weights_path)

                engine.run(agent)
                total_turns += engine.state.turn_number

                # Invariant checks
                violations = find_violations(engine.state)
                if violations or not engine.state.zone("limbo").is_empty:
                    invariant_violations += 1
                    if first_error_msg is None:
                        first_error_msg = f"Seed {seed} invariant violations: {violations}"

                # Victory tracking
                if engine.state.winner is not None:
                    winner_id = engine.state.winner
                    wins_by_seat[winner_id] = wins_by_seat.get(winner_id, 0) + 1
                    conds = satisfied_by(engine.state, winner_id)
                    cond_name = conds[0].id if conds else "unknown"
                    wins_by_condition[cond_name] = wins_by_condition.get(cond_name, 0) + 1
                else:
                    timeouts += 1

            except Exception as exc:
                errors += 1
                if first_error_msg is None:
                    first_error_msg = f"Seed {seed} raised: {type(exc).__name__}: {exc}"

            progress.advance(task_id)

    elapsed = time.perf_counter() - start_time
    rate = args.games / elapsed if elapsed > 0 else 0.0
    avg_turns = total_turns / args.games if args.games > 0 else 0.0

    # -- print results table -----------------------------------------------
    table = Table(title="Simulation Summary", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="green")

    table.add_row("Total Games", str(args.games))
    table.add_row("Completed", str(args.games - errors))
    table.add_row("Timeouts (reached turn cap)", str(timeouts))
    table.add_row(
        "Errors",
        f"[bold red]{errors}[/bold red]" if errors else "[green]0[/green]",
    )
    table.add_row(
        "Invariant Violations",
        f"[bold red]{invariant_violations}[/bold red]"
        if invariant_violations
        else "[green]0[/green]",
    )
    table.add_row("Avg Turns / Game", f"{avg_turns:.1f}")
    table.add_row("Elapsed Time", f"{elapsed:.2f}s ({rate:.1f} games/s)")

    for cond, count in sorted(wins_by_condition.items()):
        pct = (count / args.games) * 100
        table.add_row(f"Won by '{cond}'", f"{count} ({pct:.1f}%)")

    console.print()
    console.print(table)
    console.print()

    if first_error_msg:
        console.print(f"[bold red]First issue:[/bold red] {first_error_msg}")
        return 1

    if errors or invariant_violations:
        return 1

    console.print(
        "[bold green]Acceptance test PASSED: 0 errors, 0 invariant violations.[/bold green]\n"
    )
    return EXIT_OK


# ---------------------------------------------------------------------------
# new-pack
# ---------------------------------------------------------------------------


def cmd_new_pack(args: argparse.Namespace, console: Console) -> int:
    from here_to_slay.modding import new_pack

    try:
        result = new_pack(
            args.name,
            directory=args.directory,
            requires=args.requires,
            with_plugin=args.plugin,
            force=args.force,
        )
    except ContentError as exc:
        _report(console, exc.issues)
        return EXIT_USAGE

    console.print()
    console.print(f"[bold bright_green]Created[/bold bright_green] {result.root.as_posix()}")
    for path in result.files:
        console.print(f"  [dim]{path.as_posix()}[/dim]")
    console.print()
    console.print("Next:")
    console.print(f"  [cyan]uv run hts validate {result.root.as_posix()}[/cyan]")
    console.print(f"  [cyan]uv run hts diff-pack data/base {result.root.as_posix()}[/cyan]")
    console.print(f"  [cyan]uv run hts play {result.root.as_posix()}[/cyan]")
    console.print()
    console.print("[dim]The tour is in docs/modding_guide.md.[/dim]")
    return EXIT_OK


# ---------------------------------------------------------------------------
# diff-pack
# ---------------------------------------------------------------------------


def cmd_diff_pack(args: argparse.Namespace, console: Console) -> int:
    from here_to_slay.modding.diffing import diff_packs, render_value

    try:
        diff = diff_packs([args.base], [args.variant], search_paths=args.search_path)
    except ContentError as exc:
        _report(console, exc.issues)
        console.print(_summary_line(exc.issues, loaded=False))
        return EXIT_CONTENT_ERROR

    console.print()
    header = Text()
    header.append(args.base, style="bold bright_cyan")
    header.append(" -> ")
    header.append(args.variant, style="bold bright_green")
    console.print(header)
    added_packs = ", ".join(diff.added_packs) or "none"
    console.print(f"[dim]packs added: {added_packs}[/dim]")
    console.print()

    if diff.is_empty:
        console.print("[green]No differences — this pack changes nothing.[/green]\n")
        return EXIT_OK

    if diff.ops:
        table = Table(title="New ops (from plugin.py)", header_style="bold cyan")
        table.add_column("registry", style="cyan")
        table.add_column("names")
        for registry_name, names in diff.ops.items():
            table.add_row(registry_name, ", ".join(names))
        console.print(table)
        console.print()

    if diff.rules:
        table = Table(title="rules.yaml", header_style="bold cyan")
        table.add_column("", width=1)
        table.add_column("path", style="cyan", overflow="fold")
        table.add_column("base", overflow="fold")
        table.add_column("variant", overflow="fold")
        for change in diff.rules:
            # Text(), not str: a dotted path contains '[draw]', which rich would
            # otherwise swallow as a style tag and print as nothing.
            table.add_row(
                _CHANGE_MARK[change.kind],
                Text(change.path, style="cyan"),
                Text(render_value(change.before)),
                Text(render_value(change.after)),
            )
        console.print(table)
        console.print()

    if diff.cards:
        table = Table(title="cards", header_style="bold cyan")
        table.add_column("", width=1)
        table.add_column("card", style="cyan", overflow="fold")
        table.add_column("name", overflow="fold")
        table.add_column("what", overflow="fold")
        for card in diff.cards:
            what = ""
            if card.kind == "changed":
                fields = [change.path for change in card.changes]
                what = ", ".join(fields[:4])
                if len(fields) > 4:
                    what += f", +{len(fields) - 4} more"
            table.add_row(
                _CHANGE_MARK[card.kind],
                Text(card.card_id, style="cyan"),
                Text(card.name),
                Text(what),
            )
        console.print(table)
        console.print()

        if args.cards:
            for card in diff.cards_changed:
                console.print(Text(card.card_id, style="bold"))
                for change in card.changes:
                    line = Text("  ")
                    line.append(change.path, style="cyan")
                    line.append(
                        f"  {render_value(change.before)} -> {render_value(change.after)}"
                    )
                    console.print(line)
                console.print()

    console.print(
        Text(
            f"{len(diff.rules)} rule change(s), "
            f"{len(diff.cards_added)} card(s) added, "
            f"{len(diff.cards_removed)} removed, "
            f"{len(diff.cards_changed)} edited",
            style="bold",
        )
    )
    console.print()
    return EXIT_OK


#: how each kind of change is marked in the diff tables
_CHANGE_MARK: dict[str, Text] = {
    "added": Text("+", style="bold green"),
    "removed": Text("-", style="bold red"),
    "changed": Text("~", style="bold yellow"),
}


def _make_console() -> Console:
    """A console that cannot die of an unencodable character.

    A legacy Windows console runs a non-UTF-8 code page, where printing a box
    glyph or an emoji raises ``UnicodeEncodeError`` and takes the game down
    mid-board. ``errors="replace"`` degrades those to '?' instead; the ASCII
    icon fallback in ``ui/cli/render.py`` keeps the *common* glyphs readable, so
    in practice only rare decoration is affected.
    """
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
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
