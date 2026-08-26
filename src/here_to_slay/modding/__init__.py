"""Modding support: pack plugins, the ``new-pack`` scaffolder, and ``diff-pack``.

This is the one package allowed to import both ``content/`` and ``core/``.
That is not laziness — it is the bridge those two layers deliberately lack:
``content/`` may not import the engine (a ``CardDef`` must stay inert data), and
``core/`` may not read a pack directory (it takes a loaded registry and nothing
else). Importing a pack's ``plugin.py`` needs both sides at once, so it happens
here rather than by weakening either rule.

Nothing in ``ui/`` belongs here either; ``hts`` formats what these functions
return.
"""

from here_to_slay.content.vocabulary import OpKind, OpSpec, ParamSpec, Role, Vocabulary
from here_to_slay.modding.diffing import CardChange, Change, PackDiff, diff_packs
from here_to_slay.modding.plugins import (
    LoadedPlugin,
    Plugin,
    import_plugin,
    load_plugins,
    loaded_plugins,
    ops_of,
)
from here_to_slay.modding.scaffold import Scaffolded, new_pack

__all__ = [
    "CardChange",
    "Change",
    "LoadedPlugin",
    "OpKind",
    "OpSpec",
    "PackDiff",
    "ParamSpec",
    "Plugin",
    "Role",
    "Scaffolded",
    "Vocabulary",
    "diff_packs",
    "import_plugin",
    "load_plugins",
    "loaded_plugins",
    "new_pack",
    "ops_of",
]
