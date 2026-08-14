"""``hts`` — the command line entry point.

Phase 1 ships ``hts validate``. ``play``, ``replay`` and ``gui`` arrive with
their phases; they are declared here so ``--help`` tells the truth about what
exists and what does not.
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    console = Console()
    return int(args.func(args, console))


if __name__ == "__main__":
    sys.exit(main())
