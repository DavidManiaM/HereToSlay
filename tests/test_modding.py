"""Phase 10 — the modding toolchain itself.

Three separate claims, one file:

* a ``plugin.py`` reaches *both* tables — the engine registries and the
  validator's vocabulary — from one declaration, and does so idempotently;
* ``hts new-pack`` writes something that validates and plays, not a stub;
* ``hts diff-pack`` shows what a variant changes without leaving its ops
  registered in the process that asked.

``tests/test_variant_overclock.py`` is the other half: it proves the shipped
sample variant works. This file proves the tools around it do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from here_to_slay.content import ContentError, load_pack
from here_to_slay.content.errors import ContentIssue
from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.schema import PackDef, ProvidesDef
from here_to_slay.content.validate import validate_registry
from here_to_slay.content.vocabulary import BASE_VOCABULARY, Role
from here_to_slay.core.registry import CONDITIONS, EFFECTS, registered_ops, temporarily
from here_to_slay.modding import Plugin, diff_packs, import_plugin, load_plugins, new_pack
from here_to_slay.modding.diffing import flatten

# ---------------------------------------------------------------------------
# Plugin: one declaration, two tables
# ---------------------------------------------------------------------------


class TestPluginDeclares:
    def test_an_effect_reaches_the_registry_and_the_vocabulary(self) -> None:
        plugin = Plugin("t")

        @plugin.effect("t_shout", params={"text": Role.VALUE})
        def shout(ctx: Any, params: dict[str, Any]) -> Any:
            return None

        # Declared but not yet applied: nothing global has changed.
        assert "t_shout" not in EFFECTS
        assert BASE_VOCABULARY.effect("t_shout") is None

        with temporarily():
            plugin.install()
            vocabulary = plugin.extend(BASE_VOCABULARY)
            assert "t_shout" in EFFECTS
            spec = vocabulary.effect("t_shout")
            assert spec is not None
            assert spec.role_of("text") is Role.VALUE

        # And the block put it back.
        assert "t_shout" not in EFFECTS

    def test_the_decorator_returns_the_function_untouched(self) -> None:
        """A plugin module must stay ordinary Python you can call directly."""
        plugin = Plugin("t")

        @plugin.condition("t_true")
        def always(ctx: Any, params: dict[str, Any]) -> bool:
            return True

        assert always(None, {}) is True

    def test_a_mutator_also_declares_its_event(self) -> None:
        """A window may only name an event something claims to produce."""
        plugin = Plugin("t")

        @plugin.mutator("t.happened")
        def happened(state: Any, event: Any) -> None:
            return None

        assert plugin.extend(BASE_VOCABULARY).knows_event("t.happened")
        assert not BASE_VOCABULARY.knows_event("t.happened")

    def test_a_required_param_is_reported_as_required(self) -> None:
        plugin = Plugin("t")

        @plugin.effect("t_needs", params={"card": (Role.REF, True), "count": Role.VALUE})
        def needs(ctx: Any, params: dict[str, Any]) -> Any:
            return None

        spec = plugin.extend(BASE_VOCABULARY).effect("t_needs")
        assert spec is not None
        assert spec.required_params == ("card",)

    def test_declaring_nothing_leaves_the_vocabulary_identical(self) -> None:
        assert Plugin("t").extend(BASE_VOCABULARY) is BASE_VOCABULARY


class TestPluginInstallIsIdempotent:
    """Loading the same pack twice in one process must not be an error, while
    two packs claiming one name still must be."""

    def test_installing_the_same_plugin_twice_is_a_no_op(self) -> None:
        plugin = Plugin("t")

        @plugin.condition("t_dup")
        def dup(ctx: Any, params: dict[str, Any]) -> bool:
            return True

        with temporarily():
            plugin.install()
            plugin.install()
            assert "t_dup" in CONDITIONS

    def test_a_second_plugin_claiming_the_name_still_raises(self) -> None:
        first, second = Plugin("a"), Plugin("b")
        for plugin in (first, second):

            @plugin.condition("t_clash")
            def clash(ctx: Any, params: dict[str, Any]) -> bool:
                return True

        with temporarily():
            first.install()
            with pytest.raises(Exception, match="already registered"):
                second.install()

    def test_reinstalling_after_temporarily_restores_the_op(self) -> None:
        """The reason registration is deferred rather than done at import time:
        ``sys.modules`` would hand back a module whose decorators never re-run."""
        plugin = Plugin("t")

        @plugin.condition("t_again")
        def again(ctx: Any, params: dict[str, Any]) -> bool:
            return True

        with temporarily():
            plugin.install()
        assert "t_again" not in CONDITIONS
        with temporarily():
            plugin.install()
            assert "t_again" in CONDITIONS


# ---------------------------------------------------------------------------
# Importing a pack's plugin.py
# ---------------------------------------------------------------------------


class TestImportPlugin:
    def test_a_missing_file_is_a_content_error_naming_the_path(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ContentError) as exc:
            import_plugin(tmp_path / "plugin.py")
        assert "plugin.py" in str(exc.value)
        assert isinstance(exc.value.issues[0], ContentIssue)

    def test_a_broken_plugin_reports_the_exception_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "plugin.py"
        path.write_text("raise ValueError('deliberate')\n", encoding="utf-8")
        with pytest.raises(ContentError) as exc:
            import_plugin(path)
        assert "ValueError" in str(exc.value)
        assert "deliberate" in str(exc.value)

    def test_a_syntax_error_is_a_content_error_too(self, tmp_path: Path) -> None:
        path = tmp_path / "plugin.py"
        path.write_text("def broken(:\n", encoding="utf-8")
        with pytest.raises(ContentError) as exc:
            import_plugin(path)
        assert "SyntaxError" in str(exc.value)

    def test_plugins_are_found_by_type_not_by_name(self, tmp_path: Path) -> None:
        path = tmp_path / "plugin.py"
        path.write_text(
            "from here_to_slay.modding import Plugin\n"
            "anything = Plugin('t')\n"
            "@anything.condition('t_found')\n"
            "def found(ctx, params):\n"
            "    return True\n",
            encoding="utf-8",
        )
        loaded = import_plugin(path)
        assert [p.pack_id for p in loaded.plugins] == ["t"]

    def test_a_pack_without_a_plugin_gets_the_base_vocabulary_back(
        self, base_content: ContentRegistry
    ) -> None:
        assert load_plugins(base_content) is BASE_VOCABULARY

    def test_the_same_file_by_two_spellings_is_one_module(self) -> None:
        """A relative and an absolute path to one plugin must not be two plugins.

        `hts validate <abs path>` followed by `hts diff-pack data/variants/x`
        used to import the file twice, producing a second `Plugin` whose
        `install()` collided with the first one's ops — an "already registered"
        error for doing nothing wrong.
        """
        relative = Path("data/variants/overclock/plugin.py")
        first = import_plugin(relative)
        second = import_plugin(relative.resolve())
        assert first.module is second.module
        assert first.plugins[0] is second.plugins[0]

    def test_installing_a_pack_twice_by_two_spellings_does_not_collide(self) -> None:
        relative = Path("data/variants/overclock/plugin.py")
        with temporarily():
            for path in (relative, relative.resolve()):
                for plugin in import_plugin(path).plugins:
                    plugin.install()
            assert "upload_card" in EFFECTS


def test_plugin_paths_are_resolved_against_the_pack_root(tmp_path: Path) -> None:
    """``ContentRegistry.plugin_paths`` is what ``load_plugins`` walks."""
    registry = ContentRegistry(
        rules=load_pack("data/base").rules,
        cards={},
        packs=(PackDef(id="p", plugin="plugin.py", provides=ProvidesDef()),),
        roots=(tmp_path,),
    )
    assert registry.plugin_paths == (tmp_path / "plugin.py",)


# ---------------------------------------------------------------------------
# new-pack
# ---------------------------------------------------------------------------


class TestScaffolder:
    def test_a_fresh_pack_validates_and_deals(self, tmp_path: Path) -> None:
        result = new_pack("freshy", directory=tmp_path)
        assert (result.root / "pack.yaml").is_file()
        assert (result.root / "rules.yaml").is_file()
        assert (result.root / "cards" / "cards.yaml").is_file()

        registry = load_pack(result.root, search_paths=["data"])
        assert validate_registry(registry, check_art=False) == []
        assert "freshy.magic.hello_world" in registry.cards

    def test_the_plugin_template_validates_too(self, tmp_path: Path) -> None:
        """The template's ops must be real ops, or the first thing a modder
        sees after ``--plugin`` is a validation error they did not write."""
        result = new_pack("plugged", directory=tmp_path, with_plugin=True)
        registry = load_pack(result.root, search_paths=["data"])
        with temporarily():
            vocabulary = load_plugins(registry)
            assert vocabulary.condition("hand_is_empty") is not None
            assert vocabulary.effect("shout") is not None
            assert vocabulary.knows_event("plugged.shouted")
            assert validate_registry(registry, vocabulary=vocabulary, check_art=False) == []

    def test_a_standalone_pack_requires_nothing(self, tmp_path: Path) -> None:
        result = new_pack("solo", directory=tmp_path, requires=())
        text = (result.root / "pack.yaml").read_text(encoding="utf-8")
        assert "requires: []" in text

    def test_a_bad_id_is_refused_with_a_hint(self, tmp_path: Path) -> None:
        with pytest.raises(ContentError, match="lower_snake_case"):
            new_pack("Not A Slug", directory=tmp_path)

    def test_a_non_empty_directory_needs_force(self, tmp_path: Path) -> None:
        new_pack("twice", directory=tmp_path)
        with pytest.raises(ContentError, match="already exists"):
            new_pack("twice", directory=tmp_path)
        assert new_pack("twice", directory=tmp_path, force=True).files


# ---------------------------------------------------------------------------
# diff-pack
# ---------------------------------------------------------------------------


class TestFlatten:
    def test_an_id_list_is_keyed_by_id_not_by_index(self) -> None:
        tree = {"actions": [{"id": "draw", "cost": 1}, {"id": "play", "cost": 2}]}
        assert dict(flatten(tree)) == {
            "actions[draw].id": "draw",
            "actions[draw].cost": 1,
            "actions[play].id": "play",
            "actions[play].cost": 2,
        }

    def test_a_plain_list_keeps_its_indices(self) -> None:
        assert dict(flatten({"classes": ["bard", "thief"]})) == {
            "classes[0]": "bard",
            "classes[1]": "thief",
        }

    def test_inserting_an_entry_does_not_shift_the_others(self) -> None:
        """The reason ids beat indices: a variant that prepends an action must
        not report every later action as edited."""
        before = {"a": [{"id": "x", "v": 1}, {"id": "y", "v": 2}]}
        after = {"a": [{"id": "new", "v": 0}, {"id": "x", "v": 1}, {"id": "y", "v": 2}]}
        left, right = dict(flatten(before)), dict(flatten(after))
        differing = {p for p in left.keys() | right.keys() if left.get(p) != right.get(p)}
        assert differing == {"a[new].id", "a[new].v"}


class TestDiffPacks:
    def test_a_pack_against_itself_is_empty(self) -> None:
        assert diff_packs(["data/base"], ["data/base"]).is_empty

    def test_the_sample_variant_reports_every_seam(self) -> None:
        diff = diff_packs(["data/base"], ["data/variants/overclock"])
        paths = {change.path for change in diff.rules}

        assert diff.added_packs == ("overclock",)
        assert "zones[cache].scope" in paths  # new zone
        assert "actions[upload].id" in paths  # new action
        assert "windows.cache_upload.on" in paths  # new reaction window
        assert "victory[full_cache].id" in paths  # new win condition
        assert "victory[full_party].id" in paths  # ...and one removed
        assert "classes[6]" in paths  # new class

        assert diff.ops["effects"] == ("upload_card",)
        assert diff.ops["conditions"] == ("cache_size",)
        assert diff.ops["selectors"] == ("cached",)
        assert diff.ops["costs"] == ("cache_burn",)
        assert diff.ops["mutators"] == ("cache.uploaded",)

    def test_added_and_patched_cards_are_told_apart(self) -> None:
        diff = diff_packs(["data/base"], ["data/variants/overclock"])
        added = {card.card_id for card in diff.cards_added}
        edited = {card.card_id for card in diff.cards_changed}
        assert "overclock.hero.script_kiddie" in added
        assert edited == {"base.monster.dracos"}
        (dracos,) = diff.cards_changed
        assert {change.path for change in dracos.changes} == {
            "roll.outcomes[0].min",
            "roll.outcomes[1].max",
        }

    def test_diffing_registers_nothing_in_this_process(self) -> None:
        """A diff reads the `Plugin` objects; it never installs them."""
        before = registered_ops()
        diff_packs(["data/base"], ["data/variants/overclock"])
        diff_packs(["data/base"], ["data/variants/overclock"])
        assert registered_ops() == before

    def test_the_ops_are_reported_even_when_the_pack_is_already_installed(self) -> None:
        """Reading the plugin beats diffing the global tables: after an
        ``hts validate`` in the same process the tables no longer *change*, and
        a before/after diff would report a plugin that adds nothing."""
        registry = load_pack("data/variants/overclock")
        with temporarily():
            load_plugins(registry)
            diff = diff_packs(["data/base"], ["data/variants/overclock"])
        assert diff.ops["effects"] == ("upload_card",)
