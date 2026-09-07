"""Headless tests for the palette's view-models. No widgets, no QML, no Premiere."""

from __future__ import annotations

import unittest

from models import MatchInfo, ResultRowModel, SearchResultSet
from palette_view_models import PaletteViewModel, ResultsModel


def make_row(title: str, *, accent: str = "video", payload: dict | None = None) -> ResultRowModel:
    return ResultRowModel(
        payload=payload if payload is not None else {"name": title, "type": "effect_video"},
        title=title,
        subtitle=f"{title} subtitle",
        type_label="VIDEO",
        icon_kind="effect",
        is_favorite=False,
        accent_kind=accent,
        accent_color=None,
    )


class ResultsModelTests(unittest.TestCase):
    def setUp(self):
        self.model = ResultsModel()

    def test_starts_empty(self):
        self.assertEqual(self.model.rowCount(), 0)

    def test_set_rows_reports_the_new_count(self):
        self.model.set_rows([make_row("Gaussian Blur"), make_row("Warp Stabilizer")])
        self.assertEqual(self.model.rowCount(), 2)

    def test_data_returns_the_mapped_role(self):
        self.model.set_rows([make_row("Gaussian Blur")])
        index = self.model.index(0, 0)
        self.assertEqual(self.model.data(index, ResultsModel.TitleRole), "Gaussian Blur")
        self.assertEqual(self.model.data(index, ResultsModel.SubtitleRole), "Gaussian Blur subtitle")
        self.assertEqual(self.model.data(index, ResultsModel.TypeLabelRole), "VIDEO")
        self.assertEqual(self.model.data(index, ResultsModel.AccentKindRole), "video")
        self.assertIs(self.model.data(index, ResultsModel.IsFavoriteRole), False)

    def test_data_for_an_out_of_range_index_is_none(self):
        self.model.set_rows([make_row("Gaussian Blur")])
        self.assertIsNone(self.model.data(self.model.index(5, 0), ResultsModel.TitleRole))

    def test_role_names_are_camel_case_for_qml(self):
        names = self.model.roleNames()
        self.assertEqual(names[ResultsModel.TitleRole], b"title")
        self.assertEqual(names[ResultsModel.AccentColorRole], b"accentColor")
        self.assertEqual(names[ResultsModel.PayloadRole], b"payload")

    def test_payload_at_returns_the_underlying_dict(self):
        payload = {"name": "Warp Stabilizer", "type": "effect_video"}
        self.model.set_rows([make_row("Warp Stabilizer", payload=payload)])
        self.assertIs(self.model.payload_at(0), payload)

    def test_payload_at_out_of_range_is_none(self):
        self.assertIsNone(self.model.payload_at(0))

    def test_set_rows_replaces_rather_than_appends(self):
        self.model.set_rows([make_row("A"), make_row("B")])
        self.model.set_rows([make_row("C")])
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.model.data(self.model.index(0, 0), ResultsModel.TitleRole), "C")


class FakeQueryServices:
    """Stands in for the pure helpers that live in app.py."""

    CATEGORY_FILTERS = {"Video": {"effect_video"}, "Audio": {"effect_audio"}}

    def __init__(self, catalog=None):
        self.catalog = catalog if catalog is not None else []
        self.recent = ({"name": "Recent One", "type": "effect_video"},)
        self.label_items = [{"name": "Violet", "type": "timeline_action"}]
        self.aliases = {}
        self.label_queries = set()
        self.slash = {}
        self.last_type_filters = "unset"

    def resolve_alias(self, raw_query):
        return self.aliases.get(raw_query, raw_query)

    def parse_label_command(self, query):
        return "violet" if query in self.label_queries else None

    def build_label_color_items(self, label_filter):
        return list(self.label_items)

    def parse_slash_command(self, query):
        if query in self.slash:
            return self.slash[query]
        return query, None, False

    def build_recent_action_items(self):
        return self.recent

    def search(self, query, type_filters=None):
        self.last_type_filters = type_filters
        items = tuple(e for e in self.catalog if query.lower() in e["name"].lower())
        if type_filters is not None:
            items = tuple(e for e in items if e["type"] in type_filters)
        return SearchResultSet(
            items=items,
            match_infos=tuple(MatchInfo(score=1.0, ranges=()) for _ in items),
            total_count=len(items),
            visible_count=len(items),
            query=query,
        )

    def build_row_model(self, payload):
        return make_row(payload["name"], payload=payload)

    def category_type_filters(self, category):
        return self.CATEGORY_FILTERS.get(category)


CATALOG = [
    {"name": "Gaussian Blur", "type": "effect_video"},
    {"name": "Gaussian Sharpen", "type": "effect_video"},
    {"name": "Studio Reverb", "type": "effect_audio"},
]


class PaletteViewModelTests(unittest.TestCase):
    def setUp(self):
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)

    def test_empty_query_shows_recent_actions_and_results_state(self):
        self.vm.set_query("")
        self.assertEqual(self.vm.results.rowCount(), 1)
        self.assertEqual(self.vm.viewState, "results")

    def test_empty_query_with_no_recent_actions_is_idle(self):
        self.services.recent = ()
        self.vm.set_query("")
        self.assertEqual(self.vm.results.rowCount(), 0)
        self.assertEqual(self.vm.viewState, "idle")

    def test_query_filters_the_catalog(self):
        self.vm.set_query("gaussian")
        self.assertEqual(self.vm.results.rowCount(), 2)
        self.assertEqual(self.vm.viewState, "results")

    def test_query_with_no_match_is_message_state(self):
        self.vm.set_query("nothing matches this")
        self.assertEqual(self.vm.results.rowCount(), 0)
        self.assertEqual(self.vm.viewState, "message")

    def test_category_narrows_the_type_filters(self):
        self.vm.select_category("Audio")
        self.vm.set_query("e")
        self.assertEqual(self.services.last_type_filters, {"effect_audio"})

    def test_todos_category_clears_the_filter(self):
        self.vm.select_category("Audio")
        self.vm.select_category("Todos")
        self.vm.set_query("e")
        self.assertIsNone(self.vm.activeCategory)
        self.assertIsNone(self.services.last_type_filters)

    def test_slash_command_switches_the_category(self):
        self.services.slash["/audio reverb"] = ("reverb", "Audio", True)
        self.vm.set_query("/audio reverb")
        self.assertEqual(self.vm.activeCategory, "Audio")

    def test_alias_is_resolved_before_searching(self):
        self.services.aliases["gb"] = "gaussian blur"
        self.vm.set_query("gb")
        self.assertEqual(self.vm.results.rowCount(), 1)

    def test_label_command_bypasses_the_search_index(self):
        self.services.label_queries.add("/label")
        self.vm.set_query("/label")
        self.assertEqual(self.vm.results.rowCount(), 1)
        self.assertEqual(self.vm.viewState, "results")

    def test_selection_starts_at_the_first_row(self):
        self.vm.set_query("gaussian")
        self.assertEqual(self.vm.selectedIndex, 0)

    def test_move_selection_clamps_at_both_ends(self):
        self.vm.set_query("gaussian")
        self.vm.move_selection(-1)
        self.assertEqual(self.vm.selectedIndex, 0)
        self.vm.move_selection(1)
        self.assertEqual(self.vm.selectedIndex, 1)
        self.vm.move_selection(1)
        self.assertEqual(self.vm.selectedIndex, 1)

    def test_selected_payload_follows_the_selection(self):
        self.vm.set_query("gaussian")
        self.vm.move_selection(1)
        self.assertEqual(self.vm.selected_payload()["name"], "Gaussian Sharpen")

    def test_selected_payload_is_none_when_there_are_no_results(self):
        self.vm.set_query("nothing matches this")
        self.assertIsNone(self.vm.selected_payload())

    def test_query_changed_signal_fires(self):
        seen = []
        self.vm.queryChanged.connect(lambda: seen.append(self.vm.query))
        self.vm.set_query("gaussian")
        self.assertEqual(seen, ["gaussian"])

    def test_view_state_changed_signal_fires_on_transition(self):
        seen = []
        self.vm.viewStateChanged.connect(lambda: seen.append(self.vm.viewState))
        self.vm.set_query("gaussian")
        self.vm.set_query("nothing matches this")
        self.assertEqual(seen, ["results", "message"])


class AppQueryServicesTests(unittest.TestCase):
    """app.py's real services object must satisfy the QueryServices protocol."""

    def test_app_exposes_a_services_implementation(self):
        import app
        self.assertTrue(hasattr(app, "AppQueryServices"))

    def test_services_methods_are_all_present(self):
        import app
        required = (
            "resolve_alias", "parse_label_command", "build_label_color_items",
            "parse_slash_command", "build_recent_action_items", "search",
            "build_row_model", "category_type_filters",
        )
        for name in required:
            self.assertTrue(
                callable(getattr(app.AppQueryServices, name, None)),
                f"AppQueryServices is missing {name}()",
            )

    def test_category_type_filters_matches_the_palette_table(self):
        import app
        services = app.AppQueryServices(loader=None)
        for category, expected in app.CATEGORY_TYPE_FILTERS.items():
            self.assertEqual(services.category_type_filters(category), expected)
        self.assertIsNone(services.category_type_filters("Todos"))


if __name__ == "__main__":
    unittest.main()
