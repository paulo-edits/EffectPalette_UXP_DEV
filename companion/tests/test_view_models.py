"""Headless tests for the palette's view-models. No widgets, no QML, no Premiere."""

from __future__ import annotations

import unittest

from models import ResultRowModel
from palette_view_models import ResultsModel


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


if __name__ == "__main__":
    unittest.main()
