"""``hts`` — the command line entry point.

``validate`` checks a content pack, ``play`` runs the terminal client, ``gui``
opens the pygame client, ``replay`` re-runs a saved decision log, ``saves``
lists what you can resume, and ``sim`` fuzzes headless games. Every command
loads content the same way, so a variant pack works everywhere the base game
does.

A **save game is a decision log**, so ``play``, ``gui`` and ``saves`` all speak
the same file and ``replay`` will happily step through one
(``core/savegame.py``).
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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

#: Where saves live unless told otherwise. Beside ``hts_logs/`` on purpose:
#: both are decision logs, and a player should be able to find them together.
DEFAULT_SAVE_DIR = "hts_saves"

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
        help="do not write the decision log, and disable the in-game save key",
    )
    play.add_argument(
        "--load",
        default=None,
        metavar="SAVE",
        help="resume a saved game (a path, or a name in the save directory)",
    )
    play.add_argument(
        "--save-dir",
        default=DEFAULT_SAVE_DIR,
        metavar="DIR",
        help=f"where saves are written and looked for (default: {DEFAULT_SAVE_DIR})",
    )
    play.add_argument(
        "--save-label",
        default=None,
        metavar="NAME",
        help="name saves after this instead of a timestamp (overwrites on re-save)",
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
    # These four default to None rather than a number, so "not given" is
    # distinguishable from "given the same value the settings file holds" —
    # which is what lets the settings screen remember a window size and a flag
    # typed today still win over it.
    gui.add_argument(
        "--width",
        type=int,
        default=None,
        metavar="W",
        help="window width (default: remembered, else 1920; clamped to the desktop)",
    )
    gui.add_argument(
        "--height",
        type=int,
        default=None,
        metavar="H",
        help="window height (default: remembered, else 1080; clamped to the desktop)",
    )
    gui.add_argument(
        "--ui-scale",
        type=float,
        default=None,
        metavar="F",
        help="chrome scale — below 1.0 shrinks the HUD so the board grows "
             "(default: remembered, else 1.0)",
    )
    gui.add_argument(
        "--fullscreen",
        action="store_true",
        default=None,
        help="start fullscreen (F11 toggles; default: remembered)",
    )
    gui.add_argument(
        "--no-sound", action="store_true", help="start with the procedural cues muted"
    )
    gui.add_argument(
        "--reveal-all",
        action="store_true",
        help="spectator mode: show every hand (for demos and debugging)",
    )
    gui.add_argument(
        "--no-menu",
        action="store_true",
        help="skip the start screen and deal straight away, using the flags above",
    )
    gui.add_argument(
        "--load",
        default=None,
        metavar="SAVE",
        help="resume a saved game (a path, or a name in the save directory)",
    )
    gui.add_argument(
        "--watch",
        default=None,
        metavar="LOG",
        help="replay viewer: step through a saved game or decision log on the board",
    )
    gui.add_argument(
        "--save-dir",
        default=DEFAULT_SAVE_DIR,
        metavar="DIR",
        help=f"where saves are written and looked for (default: {DEFAULT_SAVE_DIR})",
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
    # saves
    # ------------------------------------------------------------------
    saves = subparsers.add_parser(
        "saves",
        help="list the games you can resume",
        description=(
            "List every readable save in the save directory, newest first. "
            "Resume one with 'hts play --load NAME' or 'hts gui --load NAME'."
        ),
    )
    saves.add_argument(
        "--save-dir",
        default=DEFAULT_SAVE_DIR,
        metavar="DIR",
        help=f"directory to list (default: {DEFAULT_SAVE_DIR})",
    )
    saves.set_defaults(func=cmd_saves)

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

    from here_to_slay.core.savegame import SaveError

    # -- load content ------------------------------------------------------
    try:
        registry, _ = load_content(args)
    except ContentError as exc:
        console.print(f"[bold red]Content error:[/bold red] {exc}")
        return EXIT_CONTENT_ERROR

    save_dir = Path(args.save_dir)

    # -- build engine, either dealt fresh or restored from a save ----------
    from here_to_slay.core.engine import Engine

    engine: Engine
    if args.load:
        try:
            engine, seed = _restore_game(args.load, registry, save_dir, console)
        except SaveError as exc:
            console.print(f"[bold red]Could not load:[/bold red] {exc}")
            return EXIT_USAGE
        names = [engine.state.player(pid).name for pid in engine.state.turn_order]
    else:
        names = _player_names(args)
        problem = _check_player_count(registry, names)
        if problem:
            console.print(problem)
            return EXIT_USAGE
        seed = args.seed if args.seed is not None else _random_seed()
        engine = Engine.new(registry, names, seed=seed, max_turns=args.max_turns)

    n_players = len(names)
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

    def _save_now() -> str:
        """Called when the player types 's' at a prompt.

        The prompt is the only moment a terminal client is at an
        ``Engine.savepoint``: the engine is blocked waiting for this answer, so
        nothing is mid-effect and the log holds every decision made so far.
        """
        return _write_save(engine, save_dir, label=args.save_label or "")

    presenter = CliPresenter(
        engine, registry, console=console,
        on_save=None if args.no_save else _save_now,
    )
    status: object = None
    # An interrupted game is still a game: the log is this project's undo and
    # replay mechanism, and discarding it on the most common way a hot-seat
    # session ends - somebody presses Ctrl+C - is the wrong trade. So the run is
    # separated from the save, and the save happens either way.
    try:
        status = engine.run(presenter)
    except KeyboardInterrupt:
        console.print("\n[yellow]Game interrupted.[/yellow]")
    except EOFError:
        # A closed stdin: a piped session that ran out of answers, or a terminal
        # that went away. `_read_int` raises rather than re-prompting forever,
        # which is right; printing its traceback at the user is not.
        console.print("\n[yellow]Input ended before the game did.[/yellow]")
    else:
        # -- result --------------------------------------------------------
        if isinstance(status, GameOver) and status.winner:
            winner_name = engine.state.player(status.winner).name
            console.print(
                f"\n[bold bright_yellow]{TROPHY} {winner_name} wins![/bold bright_yellow]\n"
            )
        else:
            console.print("\n[dim]Game over (no winner / turn cap reached).[/dim]\n")

    # -- save log ----------------------------------------------------------
    if not args.no_save and len(engine.log):
        log_dir = Path.cwd() / "hts_logs"
        log_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_seed = re.sub(r"[^\w\-]", "_", str(seed))
        log_path = log_dir / f"{ts}_{safe_seed}.json"
        engine.log.save(log_path)
        console.print(f"[dim]Decision log saved -> {log_path}[/dim]")

    return EXIT_OK


# ---------------------------------------------------------------------------
# Shared by play, gui and saves: names, seat counts, and the save directory
# ---------------------------------------------------------------------------


def _player_names(args: argparse.Namespace) -> list[str]:
    if args.names:
        return list(args.names)
    n = max(2, args.players)
    return [f"Jucător {i}" for i in range(1, n + 1)]


def _check_player_count(registry: ContentRegistry, names: Sequence[str]) -> str:
    """A message if this rule set will not seat this many players, else ''."""
    max_p = registry.rules.setup.max_players
    min_p = registry.rules.setup.min_players
    if min_p <= len(names) <= max_p:
        return ""
    return (
        f"[red]This rule set requires {min_p}-{max_p} players; "
        f"got {len(names)}.[/red]"
    )


def _resolve_save(target: str, save_dir: Path) -> Path:
    """A save named on the command line: a path, or a name in the save folder."""
    from here_to_slay.core.savegame import SAVE_SUFFIX, save_path

    direct = Path(target)
    if direct.is_file():
        return direct
    candidate = save_path(save_dir, target)
    if candidate.is_file():
        return candidate
    plain = Path(save_dir) / target
    if plain.is_file():
        return plain
    raise FileNotFoundError(
        f"no save called '{target}' - looked for {direct}, {candidate} "
        f"and {save_dir}/{target}{SAVE_SUFFIX}"
    )


def _restore_game(
    target: str, registry: ContentRegistry, save_dir: Path, console: Console
) -> tuple[Any, int | str]:
    """Load a save and replay it back to the position it was taken at."""
    from here_to_slay.core.savegame import SaveError, SaveGame

    try:
        path = _resolve_save(target, save_dir)
    except FileNotFoundError as exc:
        raise SaveError(str(exc)) from None

    game = SaveGame.load(path)
    if game.packs and set(game.packs) - set(registry.pack_ids):
        console.print(
            f"[yellow]This save was made with pack(s) {', '.join(game.packs)}; "
            f"you loaded {', '.join(registry.pack_ids)}.[/yellow]"
        )
    engine = game.restore(registry)
    console.print(
        f"[bold bright_cyan]Loaded[/bold bright_cyan] [dim]{path.name} - "
        f"{game.describe()}, {len(game.log)} decision(s) replayed[/dim]"
    )
    return engine, engine.state.rng.seed


def _write_save(engine: Any, save_dir: Path, *, label: str = "") -> str:
    """Capture and write a save, returning the line to show the player."""
    from here_to_slay.core.savegame import SaveGame, autosave_name, save_path

    game = SaveGame.capture(engine, label=label)
    name = label or autosave_name(game.summary.players, game.summary.turn_number)
    path = game.save(save_path(save_dir, name))
    return f"Saved -> {path}"


# ---------------------------------------------------------------------------
# gui
# ---------------------------------------------------------------------------


def cmd_gui(args: argparse.Namespace, console: Console) -> int:
    from here_to_slay.core.savegame import SaveError

    try:
        registry, _ = load_content(args)
    except ContentError as exc:
        console.print(f"[bold red]Content error:[/bold red] {exc}")
        return EXIT_CONTENT_ERROR

    if args.load and args.watch:
        console.print("[red]--load resumes a game and --watch replays one; pick one.[/red]")
        return EXIT_USAGE

    save_dir = Path(args.save_dir)
    from here_to_slay.ui.pygame import launch

    common = {
        "width": args.width,
        "height": args.height,
        "ui_scale": args.ui_scale,
        "fullscreen": args.fullscreen,
        "reveal_all": args.reveal_all,
        "sound": False if args.no_sound else None,
        "save_dir": save_dir,
    }

    # -- replay viewer -----------------------------------------------------
    if args.watch:
        try:
            code = _launch_watch(args.watch, registry, save_dir, console, launch, common)
        except SaveError as exc:
            console.print(f"[bold red]Could not open:[/bold red] {exc}")
            return EXIT_USAGE
        return EXIT_OK if code == 0 else EXIT_RUNTIME_ERROR

    # -- a resumed game ----------------------------------------------------
    engine = None
    if args.load:
        try:
            engine, seed = _restore_game(args.load, registry, save_dir, console)
        except SaveError as exc:
            console.print(f"[bold red]Could not load:[/bold red] {exc}")
            return EXIT_USAGE
        names = [engine.state.player(pid).name for pid in engine.state.turn_order]
    else:
        names = _player_names(args)
        problem = _check_player_count(registry, names)
        if problem:
            console.print(problem)
            return EXIT_USAGE
        seed = args.seed if args.seed is not None else _random_seed()

    n_players = len(names)
    ai_seats = max(0, min(args.ai, n_players - 1))
    if args.ai > ai_seats:
        console.print(
            f"[yellow]Only {ai_seats} of {n_players} seats can be AI — "
            f"somebody has to hold the mouse.[/yellow]"
        )

    console.print(
        f"[bold bright_green]Here to Slay[/bold bright_green]  "
        f"[dim]seed={seed!r}  {n_players} players"
        f"{f', {ai_seats} AI' if ai_seats else ''}[/dim]"
    )
    console.print(
        "[dim]I = rules  ·  L = log  ·  O = settings  ·  F2 = save  ·  F9 = load  ·  "
        "Esc = menu  ·  Ctrl+Shift+D = dev console[/dim]"
    )

    code = launch(
        registry, names,
        seed=seed, max_turns=args.max_turns, ai_seats=ai_seats,
        engine=engine,
        # The start screen is the front door: it is where a name is typed and
        # where a network game is arranged. Skipped when a save is being resumed
        # (there is a game already) and by --no-menu, which is what a demo or a
        # script wants.
        start_on_menu=not args.no_menu and engine is None,
        **common,
    )
    return EXIT_OK if code == 0 else EXIT_RUNTIME_ERROR


def _launch_watch(
    target: str,
    registry: ContentRegistry,
    save_dir: Path,
    console: Console,
    launch: Any,
    common: dict[str, Any],
) -> int:
    """Open the board as a replay viewer over a save or a plain decision log.

    Both are the same file with a different wrapper, so this accepts either:
    a save game is a log plus a header, and a log written by ``hts play`` is
    the header-less version of the same thing.
    """
    from here_to_slay.core.engine import Engine
    from here_to_slay.core.log import DecisionLog
    from here_to_slay.core.savegame import SaveError, SaveGame
    from here_to_slay.ui.pygame.replay import ReplayTransport

    try:
        path = _resolve_save(target, save_dir)
    except FileNotFoundError:
        path = Path(target)
        if not path.is_file():
            raise SaveError(f"no such log or save: {target}") from None

    try:
        log = SaveGame.load(path).log
    except SaveError:
        try:
            log = DecisionLog.load(path)
        except Exception as exc:
            raise SaveError(f"{path.name} is neither a save nor a decision log: {exc}") from None

    # Every field of a `DecisionLog` has a default, so *any* JSON object loads as
    # an empty one. Without this check, opening the wrong .json got as far as
    # dealing a game and failed with "'base' needs at least 2 players, got 0" —
    # a true message about the wrong thing.
    if not log.players:
        raise SaveError(
            f"{path.name} is neither a save nor a decision log: it names no players"
        )

    engine, source = Engine.replaying(registry, log)
    names = [engine.state.player(pid).name for pid in engine.state.turn_order]
    console.print(
        f"[bold bright_cyan]Replaying[/bold bright_cyan]  "
        f"[dim]{path.name}  ·  {len(log.entries)} decision(s)  ·  {len(names)} players[/dim]"
    )
    console.print(
        "[dim]Space = play/pause  ·  . = step  ·  +/- = speed  ·  Q/E = cameras[/dim]"
    )
    # The transport is the only seat at this table: it answers every request
    # from the log, at whatever pace the viewer asks for.
    common.pop("save_dir", None)
    return int(launch(
        registry, names,
        seed=log.seed, max_turns=log.max_turns,
        engine=engine, replay=ReplayTransport(source), **common,
    ))


# ---------------------------------------------------------------------------
# saves
# ---------------------------------------------------------------------------


def cmd_saves(args: argparse.Namespace, console: Console) -> int:
    """List what can be resumed. Reads the header only — no game is replayed.

    That is the whole reason :class:`SaveSummary` is written at save time: a
    listing that had to restore each game to describe it would replay twenty
    games to print twenty lines.
    """
    from here_to_slay.core.savegame import SAVE_SUFFIX, list_saves

    folder = Path(args.save_dir)
    games = list_saves(folder)
    if not games:
        console.print(f"[dim]No saves in {folder}/[/dim]")
        console.print("[dim]Press 's' at any prompt in 'hts play' to make one.[/dim]")
        return EXIT_OK

    table = Table(show_header=True, header_style="bold", highlight=False)
    table.add_column("name", style="cyan", overflow="fold")
    table.add_column("where", overflow="fold")
    table.add_column("saved", style="dim", overflow="fold")
    table.add_column("packs", style="dim", overflow="fold")
    for game in games:
        name = game.path.name[: -len(SAVE_SUFFIX)] if game.path else game.title
        summary = game.summary
        where = (
            f"won by {summary.winner}"
            if summary.winner
            else f"turn {summary.turn_number} - {summary.decisions} decision(s)"
        )
        table.add_row(
            name,
            where + (f" - {', '.join(summary.players)}" if summary.players else ""),
            game.saved_at.replace("T", " ")[:16],
            ", ".join(game.packs),
        )
    console.print(table)
    console.print(
        f"[dim]{len(games)} save(s) in {folder}/  -  "
        f"resume with: hts play --load <name>[/dim]"
    )
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
    exhausted = False
    try:
        status = engine.run(viewer)
    except ReplayExhausted:
        # The expected end of a partial log — a distinct type rather than a
        # substring of somebody else's message (Phase 5 gap 6). Worth saying
        # out loud, though: printing "Replay finished" over a log that ran out
        # mid-game is how a divergence hides.
        exhausted = True
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
    elif exhausted:
        console.print(
            f"[yellow]The log ran out after {len(log.entries)} decision(s), "
            f"but the game is still waiting on one.[/yellow]"
        )
        console.print(
            "[dim]Expected for a log saved from an interrupted game. Otherwise the "
            "replay diverged — check that the pack and its plugin.py are the ones "
            "the log was recorded against.[/dim]"
        )
    else:
        console.print("[dim]Replay finished.[/dim]")
    return EXIT_OK


# ---------------------------------------------------------------------------
# sim
# ---------------------------------------------------------------------------


def cmd_sim(args: argparse.Namespace, console: Console) -> int:
    import os
    import time
    from pathlib import Path

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
    # Checked once here rather than once per game: the agent is rebuilt inside
    # the loop, so a mistyped path used to be discovered `--games` times, each
    # one a caught exception and a wasted setup.
    if weights_path is not None and not Path(weights_path).is_file():
        console.print(f"[red]AI weights file not found: {weights_path}[/red]")
        return EXIT_USAGE

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
