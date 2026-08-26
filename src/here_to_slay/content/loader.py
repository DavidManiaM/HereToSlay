"""Pack loading: glob, parse, order, merge, patch, validate.

The loader never raises a bare ``KeyError``/``ValidationError`` at the caller.
Everything becomes a :class:`ContentIssue` with a path, and the whole batch is
raised at once so one run of ``hts validate`` fixes a whole file.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from here_to_slay.content.errors import ContentError, ContentIssue, Severity
from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.schema import CardDef, PackDef, PatchDef, RuleSet

PACK_FILE = "pack.yaml"

_CARD_ADAPTER: TypeAdapter[CardDef] = TypeAdapter(CardDef)


class PackLoader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans.

    PyYAML implements YAML 1.1, where ``on``, ``off``, ``yes`` and ``no`` are
    booleans. That would silently turn a trigger's ``on: monster.slain`` key
    into ``True: monster.slain`` — a bug a modder could never diagnose. Here
    only ``true``/``false`` are booleans; everything else stays a string.
    """


PackLoader.yaml_implicit_resolvers = {
    first_char: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
PackLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def parse_yaml(text: str) -> Any:
    """Parse pack YAML. See :class:`PackLoader` for the one dialect difference."""
    return yaml.load(text, Loader=PackLoader)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _display(path: Path) -> str:
    """A stable, forward-slashed path for error messages."""
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_yaml(path: Path, issues: list[ContentIssue]) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        issues.append(ContentIssue(_display(path), f"cannot read file: {exc.strerror or exc}"))
        return None
    try:
        return parse_yaml(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"{_display(path)}:{mark.line + 1}" if mark else _display(path)
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        issues.append(ContentIssue(where, f"invalid YAML: {problem}"))
        return None


def _loc_to_path(loc: Sequence[Any], drop_leading: str | None = None) -> str:
    parts = list(loc)
    if drop_leading is not None and parts and parts[0] == drop_leading:
        parts = parts[1:]
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def _validation_issues(
    exc: ValidationError, base_path: str, drop_leading: str | None = None
) -> list[ContentIssue]:
    issues: list[ContentIssue] = []
    for err in exc.errors():
        suffix = _loc_to_path(err["loc"], drop_leading)
        path = f"{base_path}.{suffix}" if suffix else base_path
        message = err["msg"]
        hint = None
        if err["type"] == "extra_forbidden":
            hint = "unknown key — check the spelling against docs/card_schemas.md"
        elif err["type"] == "union_tag_invalid":
            hint = (
                "'kind' must be one of hero, item, magic, modifier, "
                "challenge, monster, party_leader"
            )
        issues.append(ContentIssue(path, message, hint=hint))
    return issues


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` over ``base``.

    * dicts merge recursively
    * lists of ``{id: ...}`` objects merge **by id** (so a variant can re-cost a
      single action without restating the whole table); an entry with
      ``remove: true`` drops the matching base entry
    * everything else replaces
    """
    out = dict(base)
    for key, value in overlay.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = _deep_merge(current, value)
        elif _is_id_list(current) and _is_id_list(value):
            out[key] = _merge_id_lists(current, value)
        else:
            out[key] = value
    return out


def _is_id_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, dict) and "id" in item for item in value)
    )


def _merge_id_lists(base: list[dict], overlay: list[dict]) -> list[dict]:
    by_id = {item["id"]: dict(item) for item in base}
    order = [item["id"] for item in base]
    for item in overlay:
        item_id = item["id"]
        if item.get("remove") is True:
            by_id.pop(item_id, None)
            if item_id in order:
                order.remove(item_id)
            continue
        patch = {k: v for k, v in item.items() if k != "remove"}
        if item_id in by_id:
            by_id[item_id] = _deep_merge(by_id[item_id], patch)
        else:
            by_id[item_id] = patch
            order.append(item_id)
    return [by_id[item_id] for item_id in order]


def _set_dotted(target: dict[str, Any], dotted: str, value: Any) -> str | None:
    """Set ``a.b.0.c`` inside a nested structure. Returns an error message or None."""
    parts = dotted.split(".")
    node: Any = target
    for index, part in enumerate(parts[:-1]):
        key: Any = int(part) if part.lstrip("-").isdigit() else part
        try:
            if isinstance(key, int):
                node = node[key]
            else:
                node = node.setdefault(key, {})
        except (KeyError, IndexError, TypeError, AttributeError):
            return f"cannot resolve '{'.'.join(parts[: index + 1])}'"
    last = parts[-1]
    try:
        if last.lstrip("-").isdigit() and isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    except (KeyError, IndexError, TypeError, AttributeError):
        return f"cannot assign '{dotted}'"
    return None


# ---------------------------------------------------------------------------
# Pack discovery & ordering
# ---------------------------------------------------------------------------


def _pack_dir(path: Path) -> Path:
    return path.parent if path.is_file() else path


def _discover(search_roots: Iterable[Path]) -> dict[str, Path]:
    """Map pack id -> directory, by scanning for ``pack.yaml`` up to 2 deep."""
    found: dict[str, Path] = {}
    for root in search_roots:
        if not root.is_dir():
            continue
        for candidate in (*root.glob(f"*/{PACK_FILE}"), *root.glob(f"*/*/{PACK_FILE}")):
            try:
                data = parse_yaml(candidate.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                found.setdefault(data["id"], candidate.parent)
    return found


def _order_packs(
    packs: dict[str, tuple[PackDef, Path]], issues: list[ContentIssue]
) -> list[tuple[PackDef, Path]]:
    """Deterministic topological order: dependencies first, ties by pack id."""
    ordered: list[tuple[PackDef, Path]] = []
    visited: set[str] = set()
    visiting: list[str] = []

    def visit(pack_id: str) -> None:
        if pack_id in visited:
            return
        if pack_id in visiting:
            cycle = " -> ".join([*visiting[visiting.index(pack_id) :], pack_id])
            issues.append(ContentIssue(pack_id, f"circular pack dependency: {cycle}"))
            return
        entry = packs.get(pack_id)
        if entry is None:
            return
        visiting.append(pack_id)
        pack, _ = entry
        for dep in sorted({*pack.requires, *pack.load_after}):
            if dep in packs:
                visit(dep)
        visiting.pop()
        visited.add(pack_id)
        ordered.append(entry)

    for pack_id in sorted(packs):
        visit(pack_id)
    return ordered


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_pack_def(pack_dir: Path, issues: list[ContentIssue]) -> PackDef | None:
    manifest = pack_dir / PACK_FILE
    if not manifest.is_file():
        issues.append(ContentIssue(_display(pack_dir), f"no {PACK_FILE} in this directory"))
        return None
    data = _read_yaml(manifest, issues)
    if data is None:
        return None
    if not isinstance(data, dict):
        issues.append(ContentIssue(_display(manifest), "pack.yaml must be a mapping"))
        return None
    try:
        return PackDef.model_validate(data)
    except ValidationError as exc:
        issues.extend(_validation_issues(exc, _display(manifest)))
        return None


def _implicit_roots(requested: Sequence[Path]) -> list[Path]:
    """Where to look for a ``requires:`` nobody named a search path for.

    A pack's own directory, its neighbours, and one level above them — which is
    exactly what makes ``hts validate data/variants/overclock`` find
    ``data/base`` without the modder having to learn ``--search-path`` on their
    first run. Two levels is the same depth :func:`_discover` globs, so this
    widens *where* the scan starts, never how deep it goes.
    """
    roots: list[Path] = []
    for path in requested:
        parent = _pack_dir(path).parent
        for candidate in (parent, parent.parent):
            if candidate not in roots:
                roots.append(candidate)
    return roots


def _collect_pack_graph(
    requested: Sequence[Path], search_paths: Sequence[Path], issues: list[ContentIssue]
) -> dict[str, tuple[PackDef, Path]]:
    roots = [*search_paths, *_implicit_roots(requested)]
    index = _discover(roots)
    packs: dict[str, tuple[PackDef, Path]] = {}
    queue: list[Path] = [_pack_dir(p) for p in requested]

    while queue:
        pack_dir = queue.pop(0)
        pack = _load_pack_def(pack_dir, issues)
        if pack is None:
            continue
        if pack.id in packs:
            continue
        packs[pack.id] = (pack, pack_dir)
        for dep in pack.requires:
            if dep in packs:
                continue
            dep_dir = index.get(dep)
            if dep_dir is None:
                issues.append(
                    ContentIssue(
                        f"{_display(pack_dir / PACK_FILE)}.requires",
                        f"required pack '{dep}' not found",
                        hint=f"searched {', '.join(_display(r) for r in roots) or 'nothing'}",
                    )
                )
                continue
            queue.append(dep_dir)
    return packs


def _card_files(pack: PackDef, pack_dir: Path, issues: list[ContentIssue]) -> list[Path]:
    files: list[Path] = []
    for pattern in pack.provides.cards:
        matches = sorted(pack_dir.glob(pattern))
        if not matches:
            issues.append(
                ContentIssue(
                    f"{_display(pack_dir / PACK_FILE)}.provides.cards",
                    f"pattern '{pattern}' matched no files",
                    Severity.WARNING,
                )
            )
        files.extend(matches)
    return files


def _load_cards(
    pack: PackDef,
    pack_dir: Path,
    raw_cards: dict[str, dict[str, Any]],
    sources: dict[str, str],
    owners: dict[str, str],
    issues: list[ContentIssue],
) -> None:
    for path in _card_files(pack, pack_dir, issues):
        data = _read_yaml(path, issues)
        if data is None:
            continue
        if isinstance(data, dict) and "cards" in data:
            entries = data["cards"]
        else:
            entries = data
        if not isinstance(entries, list):
            issues.append(
                ContentIssue(
                    _display(path),
                    "a card file must be a list of cards (or a mapping with a 'cards:' list)",
                )
            )
            continue

        for index, entry in enumerate(entries):
            where = f"{_display(path)}[{index}]"
            if not isinstance(entry, dict):
                issues.append(ContentIssue(where, "each card must be a mapping"))
                continue
            card_id = entry.get("id")
            if not isinstance(card_id, str):
                issues.append(ContentIssue(where, "card is missing a string 'id'"))
                continue
            if card_id in raw_cards:
                issues.append(
                    ContentIssue(
                        where,
                        f"duplicate card id '{card_id}' (already defined in {sources[card_id]})",
                        hint="use a 'patches:' entry in pack.yaml to modify another pack's card",
                    )
                )
                continue
            raw_cards[card_id] = entry
            sources[card_id] = where
            owners[card_id] = pack.id


def _apply_patches(
    pack: PackDef,
    pack_dir: Path,
    raw_cards: dict[str, dict[str, Any]],
    raw_rules: dict[str, Any],
    sources: dict[str, str],
    issues: list[ContentIssue],
) -> None:
    manifest = _display(pack_dir / PACK_FILE)
    for index, patch in enumerate(pack.patches):
        where = f"{manifest}.patches[{index}]"
        target = _patch_target(patch, raw_cards, raw_rules, where, issues)
        if target is None:
            continue
        if patch.remove:
            if patch.target != "rules":
                raw_cards.pop(patch.target, None)
                sources.pop(patch.target, None)
            else:
                issues.append(ContentIssue(where, "'rules' cannot be removed"))
            continue
        for dotted, value in patch.set.items():
            error = _set_dotted(target, dotted, value)
            if error:
                issues.append(ContentIssue(f"{where}.set.{dotted}", error))
            elif patch.target != "rules":
                sources[patch.target] = (
                    f"{sources.get(patch.target, patch.target)} (patched by {pack.id})"
                )


def _patch_target(
    patch: PatchDef,
    raw_cards: dict[str, dict[str, Any]],
    raw_rules: dict[str, Any],
    where: str,
    issues: list[ContentIssue],
) -> dict[str, Any] | None:
    if patch.target == "rules":
        return raw_rules
    target = raw_cards.get(patch.target)
    if target is None:
        issues.append(
            ContentIssue(
                f"{where}.target",
                f"no such card '{patch.target}'",
                hint="patches are applied after all required packs load",
            )
        )
    return target


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_packs(
    paths: Sequence[str | os.PathLike[str]],
    *,
    search_paths: Sequence[str | os.PathLike[str]] = (),
) -> ContentRegistry:
    """Load and structurally validate one or more packs plus their dependencies.

    Raises :class:`ContentError` carrying *every* problem found. Semantic checks
    (unknown ops, unbindable refs) are a separate pass — see
    :func:`here_to_slay.content.validate.validate_registry`.
    """
    issues: list[ContentIssue] = []
    requested = [Path(p) for p in paths]
    for path in requested:
        if not path.exists():
            issues.append(ContentIssue(_display(path), "path does not exist"))
    if issues:
        raise ContentError(issues, "content failed to load")

    packs = _collect_pack_graph(requested, [Path(p) for p in search_paths], issues)
    if not packs and not issues:
        issues.append(ContentIssue(", ".join(_display(p) for p in requested), "no packs found"))
    ordered = _order_packs(packs, issues)

    raw_rules: dict[str, Any] = {}
    raw_cards: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    owners: dict[str, str] = {}

    for pack, pack_dir in ordered:
        if pack.provides.rules:
            rules_path = pack_dir / pack.provides.rules
            data = _read_yaml(rules_path, issues)
            if isinstance(data, dict):
                raw_rules = _deep_merge(raw_rules, data)
            elif data is not None:
                issues.append(ContentIssue(_display(rules_path), "rules must be a mapping"))
        _load_cards(pack, pack_dir, raw_cards, sources, owners, issues)

    for pack, pack_dir in ordered:
        _apply_patches(pack, pack_dir, raw_cards, raw_rules, sources, issues)

    if not raw_rules:
        issues.append(
            ContentIssue(
                ", ".join(_display(p) for p in requested),
                "no pack provided a rules file",
                hint="set provides.rules in pack.yaml",
            )
        )
        rules = RuleSet(id="empty")
    else:
        rules = _validate_rules(raw_rules, ordered, issues)

    cards: dict[str, CardDef] = {}
    for card_id, raw in raw_cards.items():
        try:
            cards[card_id] = _CARD_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            issues.extend(
                _validation_issues(exc, sources.get(card_id, card_id), drop_leading=raw.get("kind"))
            )

    errors = [issue for issue in issues if issue.is_error]
    if errors:
        raise ContentError(issues, "content failed to load")

    return ContentRegistry(
        rules=rules,
        cards=cards,
        packs=tuple(pack for pack, _ in ordered),
        sources=sources,
        roots=tuple(pack_dir for _, pack_dir in ordered),
    )


def _validate_rules(
    raw_rules: dict[str, Any],
    ordered: Sequence[tuple[PackDef, Path]],
    issues: list[ContentIssue],
) -> RuleSet:
    rules_source = next(
        (
            _display(pack_dir / pack.provides.rules)
            for pack, pack_dir in reversed(ordered)
            if pack.provides.rules
        ),
        "rules.yaml",
    )
    try:
        return RuleSet.model_validate(raw_rules)
    except ValidationError as exc:
        issues.extend(_validation_issues(exc, rules_source))
        return RuleSet(id=str(raw_rules.get("id", "invalid")))


def load_pack(
    path: str | os.PathLike[str],
    *,
    search_paths: Sequence[str | os.PathLike[str]] = (),
) -> ContentRegistry:
    """Load a single pack (and everything it ``requires``)."""
    return load_packs([path], search_paths=search_paths)
