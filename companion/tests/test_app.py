"""Off-host tests for the companion's catalog loader, search index and pure helpers (app.py)."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

import app


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.paths = dataclasses.replace(
            app.DataPaths(),
            data_dir=data,
            uxp_transitions_file=data / "uxp_video_transitions.json",
            uxp_favorites_file=data / "uxp_favorites.json",
            uxp_effects_file=data / "uxp_effects.json",
            uxp_project_items_file=data / "uxp_project_items.json",
            uxp_presets_file=data / "uxp_presets.json",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_fallback_until_the_plugin_has_written_a_catalog(self):
        loader = app.EffectsLoader(self.paths)
        self.assertEqual(loader.source, "fallback")
        self.assertEqual(loader.snapshot.connection_state, "offline")
        self.assertEqual(loader.count, len(app.FALLBACK_EFFECTS))
        self.assertEqual(loader.preset_count, 0)

    def test_uxp_catalogs_are_the_only_source(self):
        write_json(self.paths.uxp_effects_file, {"effects": [
            {"name": "Gaussian Blur", "category": "Video", "type": "video"},
            {"name": "Reverb", "category": "Audio", "type": "audio"},
            {"name": "ignored", "category": "x", "type": "transition_audio"},
        ]})
        write_json(self.paths.uxp_transitions_file, {"transitions": [
            {"name": "(Adobe) Cross Dissolve", "matchName": "ADBE Cross Dissolve"},
            {"name": "missing matchName"},
        ]})
        write_json(self.paths.uxp_presets_file, {"presets": [{"name": "Zoom In", "category": "Presets"}]})
        write_json(self.paths.uxp_project_items_file, {"items": [{"name": "Clip A", "treePath": "/Clip A"}]})
        write_json(self.paths.uxp_favorites_file, {"items": [{"name": "Logo", "mediaPath": "C:/logo.png"}]})

        loader = app.EffectsLoader(self.paths)
        self.assertEqual(loader.source, "uxp")
        self.assertEqual(loader.snapshot.connection_state, "connected")
        names = {item["name"]: item for item in loader.snapshot.effects}
        self.assertEqual(set(names), {"Gaussian Blur", "Reverb", "(Adobe) Cross Dissolve"})
        self.assertEqual(names["(Adobe) Cross Dissolve"]["matchName"], "ADBE Cross Dissolve")
        self.assertEqual(loader.preset_count, 1)
        self.assertEqual(loader.project_item_count, 1)
        self.assertEqual(loader.favorite_item_count, 1)
        self.assertEqual(loader.generic_item_count, len(app.GENERIC_ITEMS))
        self.assertEqual(loader.snapshot.load_issues, ())

    def test_corrupt_catalog_is_reported_not_fatal(self):
        self.paths.uxp_effects_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.uxp_effects_file.write_text("{not json", encoding="utf-8")
        write_json(self.paths.uxp_transitions_file, {"transitions": [{"name": "Dip", "matchName": "ADBE Dip"}]})
        loader = app.EffectsLoader(self.paths)
        self.assertEqual(loader.source, "uxp")
        self.assertEqual(loader.snapshot.connection_state, "problem")
        self.assertTrue(any(issue.startswith("effects:") for issue in loader.snapshot.load_issues))

    def test_reload_tracks_file_changes(self):
        loader = app.EffectsLoader(self.paths)
        self.assertFalse(loader.needs_reload())
        write_json(self.paths.uxp_effects_file, {"effects": [{"name": "Sharpen", "type": "video"}]})
        self.assertTrue(loader.needs_reload())

    def test_search_tiers(self):
        write_json(self.paths.uxp_effects_file, {"effects": [
            {"name": "Gaussian Blur", "type": "video"},
            {"name": "Directional Blur", "type": "video"},
            {"name": "Blur", "type": "video"},
            {"name": "Sharpen", "type": "video"},
            {"name": "Reverb", "type": "audio"},
        ]})
        loader = app.EffectsLoader(self.paths)
        result = loader.search("blur")
        names = [item["name"] for item in result.items]
        self.assertEqual(names[0], "Blur", "exact name ranks first")
        self.assertIn("Gaussian Blur", names)
        self.assertIn("Directional Blur", names)
        self.assertNotIn("Sharpen", names)
        self.assertEqual(result.total_count, 3)
        self.assertEqual(result.visible_count, 3)
        self.assertEqual(len(result.match_infos), 3)

        audio_only = loader.search("re", type_filters={"audio"})
        self.assertEqual([item["name"] for item in audio_only.items], ["Reverb"])
        self.assertEqual(loader.search("   ").total_count, 0)


class HelperTests(unittest.TestCase):
    def test_parse_slash_command(self):
        self.assertEqual(app.parse_slash_command("/v blur"), ("blur", "Video", True))
        self.assertEqual(app.parse_slash_command("/all blur")[1], None)
        query, category, matched = app.parse_slash_command("blur")
        self.assertEqual((query, matched), ("blur", False))

    def test_alias_resolution(self):
        entries = [{"alias": "gb", "target": "Gaussian Blur"}]
        self.assertEqual(app.resolve_alias_query("gb", entries), "Gaussian Blur")
        self.assertEqual(app.resolve_alias_query("GB ", entries), "Gaussian Blur")
        self.assertEqual(app.resolve_alias_query("other", entries), "other")

    def test_result_row_model(self):
        preset = app.build_result_row_model({"name": "Zoom", "type": "preset", "category": "Presets"})
        self.assertEqual((preset.type_label, preset.icon_kind, preset.subtitle), ("Preset", "preset", "Presets"))
        self.assertFalse(preset.is_favorite)
        favorite = app.build_result_row_model({"name": "Logo", "type": "favorite_item", "sourceTreePath": "/Logos"})
        self.assertTrue(favorite.is_favorite)
        self.assertEqual(favorite.subtitle, "/Logos")
        label = app.build_result_row_model({"name": "Violet", "type": "label_color", "labelColor": "#ff00ff"})
        self.assertEqual(label.accent_color, "#ff00ff")

    def test_apply_status_timeout_uses_adapter_table_plus_grace(self):
        self.assertEqual(
            app.apply_status_timeout_ms({"type": "preset"}),
            app.timeout_seconds_for_effect({"type": "preset"}) * 1000.0 + app.APPLY_STATUS_TIMEOUT_GRACE_MS,
        )

    def test_format_apply_failure_maps_known_codes(self):
        self.assertEqual(app.format_apply_failure("error_no_selection"), app.tr("bridge_error_no_selection"))
        self.assertEqual(app.format_apply_failure("error_something_new"), app.tr("bridge_error_generic"))
        self.assertEqual(app.format_apply_failure(None), app.tr("status_no_response"))

    def test_resolve_nest_mode(self):
        class Adapter:
            def __init__(self, answer):
                self.answer = answer

            def has_multi_track_audio_selection(self):
                return self.answer

        original = app.find_premiere_command_shortcut
        try:
            app.find_premiere_command_shortcut = lambda name: (object(), Path("profile.kys"))
            self.assertEqual(app.resolve_nest_mode("api"), "api")
            self.assertEqual(app.resolve_nest_mode("premiere"), "premiere")
            self.assertEqual(app.resolve_nest_mode("auto", Adapter(True)), "api")
            self.assertEqual(app.resolve_nest_mode("auto", Adapter(False)), "premiere")
            self.assertEqual(app.resolve_nest_mode("auto", Adapter(None)), "premiere")
            app.find_premiere_command_shortcut = lambda name: (None, None)
            self.assertEqual(app.resolve_nest_mode("auto", Adapter(False)), "api")
        finally:
            app.find_premiere_command_shortcut = original

    def test_no_cep_bridge_left(self):
        for name in ("PremiereExecutionAdapter", "send_command", "read_bridge_status", "BRIDGE_FILE", "EffectPalette", "tk"):
            self.assertFalse(hasattr(app, name), f"{name} should be gone")


class RowIconTests(unittest.TestCase):
    """Each item type gets its own icon, and transitions their own colour."""

    ICONS = {
        "video": "fx",
        "audio": "audio",
        "transition_video": "transition",
        "transition_audio": "transition",
        "preset": "preset",
        "project_item": "project",
        "generic_item": "favorite",
        "favorite_item": "favorite",
        "timeline_action": "layers",
        "label_group_action": "label",
        "label_color": "label",
    }

    def setUp(self):
        self.original = app.CURRENT_LANGUAGE

    def tearDown(self):
        app.apply_language(self.original)

    def test_each_type_gets_its_own_icon(self):
        for item_type, expected in self.ICONS.items():
            row = app.build_result_row_model({"name": "X", "type": item_type, "labelColor": "#A17FE0"})
            self.assertEqual(row.icon_kind, expected, item_type)

    def test_transitions_have_their_own_colour(self):
        # They used to share the neutral "all" key with actions and labels.
        colour = app.get_filter_palette_color(
            app.build_result_row_model({"name": "X", "type": "transition_video"}).accent_kind)
        others = {app.get_filter_palette_color(key) for key in ("Todos", "Video", "Audio")}
        self.assertNotIn(colour, others)

    def test_internal_category_subtitles_are_translated(self):
        # GENERIC_ITEMS carry the internal "Favoritos" key as their category.
        app.apply_language("en")
        row = app.build_result_row_model({"name": "Adjustment Layer", "type": "generic_item",
                                          "category": "Favoritos"})
        self.assertEqual(row.subtitle, "Favorites")
        effect = app.build_result_row_model({"name": "X", "type": "video", "category": "Blur & Sharpen"})
        self.assertEqual(effect.subtitle, "Blur & Sharpen")

    def test_internal_keys_inside_a_path_are_translated(self):
        # The transitions catalog files items under "Transicoes > Video".
        app.apply_language("en")
        row = app.build_result_row_model({"name": "Split", "type": "transition_video",
                                          "category": "Transicoes > Video"})
        self.assertEqual(row.subtitle, "Transitions > Video")
        preset = app.build_result_row_model({"name": "Look", "type": "preset",
                                             "category": "1. Finzar > 2. Character Animation"})
        self.assertEqual(preset.subtitle, "1. Finzar > 2. Character Animation")


class LanguageSwitchTests(unittest.TestCase):
    """Picking a language in the tray reaches every palette string, with no restart."""

    PT_BADGES = {
        "preset": "Preset",
        "transition_video": "Transição",
        "project_item": "Projeto",
        "label_color": "Label",
        "label_group_action": "Ação",
        "timeline_action": "Ação",
        "favorite_item": "Favorito",
        "video": "Efeito",
    }

    def setUp(self):
        self.original = app.CURRENT_LANGUAGE

    def tearDown(self):
        app.apply_language(self.original)

    def _badge(self, item_type):
        return app.build_result_row_model({"name": "X", "type": item_type}).type_label

    def test_badges_follow_the_language(self):
        app.apply_language("en")
        self.assertEqual(self._badge("video"), "Effect")
        app.apply_language("pt")
        self.assertEqual(self._badge("video"), "Efeito")

    def test_every_badge_is_translated(self):
        app.apply_language("pt")
        for item_type, expected in self.PT_BADGES.items():
            self.assertEqual(self._badge(item_type), expected, item_type)

    def test_fallback_subtitles_use_the_display_label(self):
        # Items without a category of their own fall back to a category key, which
        # must be shown translated, not as the internal Portuguese key.
        app.apply_language("en")
        for item_type, expected in (("transition_video", "Transitions"), ("project_item", "Project"),
                                    ("favorite_item", "Favorites"), ("preset", "Presets")):
            subtitle = app.build_result_row_model({"name": "X", "type": item_type}).subtitle
            self.assertEqual(subtitle, expected, item_type)

    def test_apply_language_renames_the_nest_action(self):
        # TIMELINE_ACTIONS is built at import; its name must follow a later switch.
        for lang in ("pt", "en"):
            app.apply_language(lang)
            nest = next(a for a in app.TIMELINE_ACTIONS if a.get("action") == "nest")
            self.assertEqual(nest["name"], app.STRINGS["timeline_action_nest"][lang])

    def test_apply_language_never_saves_but_set_language_does(self):
        from unittest import mock
        other = "pt" if self.original != "pt" else "en"
        with mock.patch.object(app, "_save_language") as save:
            app.apply_language(other)
            save.assert_not_called()
            app.set_language(self.original)
            save.assert_called_once_with(self.original)

    def test_unknown_language_is_ignored(self):
        app.apply_language("xx")
        self.assertEqual(app.CURRENT_LANGUAGE, self.original)


if __name__ == "__main__":
    unittest.main()
