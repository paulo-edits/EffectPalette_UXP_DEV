"""The shared dataclasses must live in models.py and stay importable from app.py."""

from __future__ import annotations

import dataclasses
import inspect
import unittest

import models


class ModelsModuleTests(unittest.TestCase):
    def test_dataclasses_are_frozen(self):
        for cls in (models.MatchInfo, models.SearchResultSet, models.ResultRowModel, models.PaletteLayoutMetrics):
            self.assertTrue(dataclasses.is_dataclass(cls), f"{cls.__name__} is not a dataclass")
            self.assertTrue(cls.__dataclass_params__.frozen, f"{cls.__name__} is not frozen")

    def test_models_does_not_import_qt(self):
        # The point of this module is that it is importable without a Qt application.
        source = inspect.getsource(models)
        self.assertNotIn("PySide6", source)

    def test_result_row_model_accent_color_defaults_to_none(self):
        row = models.ResultRowModel(
            payload={}, title="t", subtitle="s", type_label="L",
            icon_kind="effect", is_favorite=False, accent_kind="video",
        )
        self.assertIsNone(row.accent_color)

    def test_app_still_re_exports_the_ones_it_uses(self):
        # PaletteLayoutMetrics is deliberately absent: it was re-exported for the
        # QtWidgets palette, which the QML rewrite deleted. qml_host takes it from
        # models directly now.
        import app
        self.assertIs(app.ResultRowModel, models.ResultRowModel)
        self.assertIs(app.SearchResultSet, models.SearchResultSet)
        self.assertIs(app.MatchInfo, models.MatchInfo)


if __name__ == "__main__":
    unittest.main()
