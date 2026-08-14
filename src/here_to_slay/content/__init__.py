"""Content layer: pydantic schemas, the pack loader, and the semantic validator.

This package describes *data*. It must never import :mod:`here_to_slay.core` —
a ``CardDef`` is inert; behaviour lives in the interpreter, keyed by the strings
found here.
"""

from here_to_slay.content.errors import ContentError, ContentIssue, Severity
from here_to_slay.content.loader import load_pack, load_packs
from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.validate import validate_registry

__all__ = [
    "ContentError",
    "ContentIssue",
    "ContentRegistry",
    "Severity",
    "load_pack",
    "load_packs",
    "validate_registry",
]
