"""The three-layer rule, asserted by walking the import graph.

    ui/, ai/  ->  core/  ->  content/

See ``docs/architecture_notes.md §1``. These tests are cheap now and will be the
thing that keeps the engine headless and deterministic once ``core/`` exists.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "here_to_slay"

#: layer -> module prefixes it may never import
#:
#: ``modding/`` is the one package allowed to touch both ``content`` and
#: ``core``: importing a pack's ``plugin.py`` needs the pack directory (which
#: ``core`` may not read) and the engine registries (which ``content`` may not
#: import), so the bridge lives there rather than in either of them. It is still
#: forbidden the layers above it — ``hts`` formats what it returns.
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "content": ("here_to_slay.core", "here_to_slay.ui", "here_to_slay.ai", "pygame", "rich"),
    "core": ("here_to_slay.ui", "here_to_slay.ai", "pygame", "rich", "random"),
    "ai": ("here_to_slay.ui", "pygame", "rich"),
    "modding": ("here_to_slay.ui", "here_to_slay.ai", "pygame", "rich"),
}


def imports_of(path: Path) -> Iterator[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module, node.lineno


def modules_in(layer: str) -> list[Path]:
    directory = SRC / layer
    return sorted(directory.rglob("*.py")) if directory.is_dir() else []


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_across_the_line(layer: str) -> None:
    files = modules_in(layer)
    if not files:
        pytest.skip(f"{layer}/ does not exist yet")

    violations = [
        f"{path.relative_to(SRC).as_posix()}:{lineno} imports {module}"
        for path in files
        for module, lineno in imports_of(path)
        for forbidden in FORBIDDEN[layer]
        if module == forbidden or module.startswith(f"{forbidden}.")
    ]
    assert not violations, "\n".join(violations)


def test_content_is_importable_without_the_engine() -> None:
    """content/ is inert data: a CardDef has no behaviour to depend on."""
    import here_to_slay.content as content

    assert content.load_pack is not None
