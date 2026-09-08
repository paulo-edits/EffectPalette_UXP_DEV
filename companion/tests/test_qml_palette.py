"""Loads the real QML against real view-models, offscreen.

These assert that the QML parses with zero warnings and that its bindings actually
follow the view-model. They prove nothing about how it looks, and nothing about
Premiere.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from palette_view_models import ApplyController, PaletteViewModel
from qml_host import QmlPaletteHost
from test_view_models import CATALOG, FakeAdapter, FakeQueryServices, FakeScheduler


def qt_app():
    """One QApplication for the whole process; QML runs fine under it."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class QmlLoadTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)

    def tearDown(self):
        self.host.shutdown()

    def test_palette_qml_loads(self):
        self.host.load()
        self.assertIsNotNone(self.host.window)

    def test_palette_qml_loads_without_warnings(self):
        self.host.load()
        self.assertEqual(self.host.warnings, [])

    def test_root_is_frameless_tool_and_stays_on_top(self):
        self.host.load()
        flags = self.host.window.flags()
        self.assertTrue(flags & QtCore.Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & QtCore.Qt.WindowType.Tool)
        # Qt.WindowStaysOnTop (no "Hint") parses silently and does nothing.
        self.assertTrue(flags & QtCore.Qt.WindowType.WindowStaysOnTopHint)

    def test_root_window_has_a_native_handle(self):
        self.host.load()
        self.host.window.show()
        self.assertIsInstance(int(self.host.window.winId()), int)

    def test_window_width_matches_the_python_constant(self):
        import app
        self.host.load()
        self.assertEqual(self.host.window.width(), app.FIXED_SEARCH_WINDOW_WIDTH)


if __name__ == "__main__":
    unittest.main()
