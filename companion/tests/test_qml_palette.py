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



class ThemeTokenTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(self.vm, self.apply, self.services.translate)
        self.host.load()

    def tearDown(self):
        self.host.shutdown()

    def _theme(self):
        return self.host.window.property("themeProbe")

    def test_theme_exposes_every_documented_token(self):
        theme = self._theme()
        self.assertIsNotNone(theme, "Palette.qml must expose Theme as themeProbe")
        for name in (
            "surface", "surfaceRaised", "surfaceOverlay", "border",
            "text", "textMuted", "textFaint", "accent", "success", "warning", "offline",
            "spaceXs", "spaceSm", "spaceMd", "spaceLg", "spaceXl",
            "radiusSm", "radiusMd", "radiusLg", "radiusPill",
            "fontFamily", "sizeCaption", "sizeBody", "sizeTitle", "sizeDisplay",
            "durFast", "durBase", "durSlow",
        ):
            self.assertIsNotNone(theme.property(name), f"Theme.{name} is missing")

    def test_motion_durations_are_ordered(self):
        theme = self._theme()
        self.assertLess(theme.property("durFast"), theme.property("durBase"))
        self.assertLess(theme.property("durBase"), theme.property("durSlow"))

    def test_animations_flag_follows_the_preference(self):
        theme = self._theme()
        self.assertIsInstance(theme.property("animationsEnabled"), bool)


class PaletteMetricsTests(unittest.TestCase):
    def test_metrics_mirror_the_python_constants(self):
        import app
        from qml_host import PaletteMetrics
        m = PaletteMetrics(animations_enabled=True)
        self.assertEqual(m.windowWidth, app.FIXED_SEARCH_WINDOW_WIDTH)
        self.assertEqual(m.resultsHeight, app.RESULTS_EXPANDED_HEIGHT)
        self.assertEqual(m.openAnimationMs, app.OPEN_ANIMATION_MS)
        self.assertTrue(m.animationsEnabled)

    def test_accent_for_returns_a_colour_string(self):
        from qml_host import PaletteMetrics
        m = PaletteMetrics(animations_enabled=False)
        for kind in ("video", "audio", "preset", "project", "favorite"):
            self.assertTrue(m.accentFor(kind).startswith("#"), kind)

    def test_animations_flag_is_carried_through(self):
        from qml_host import PaletteMetrics
        self.assertFalse(PaletteMetrics(animations_enabled=False).animationsEnabled)

if __name__ == "__main__":
    unittest.main()
