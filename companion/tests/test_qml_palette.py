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

    def test_shell_width_matches_the_python_constant(self):
        import app
        self.host.load()
        window = self.host.window
        frame = window.findChild(QtCore.QObject, "shellFrame")
        self.assertIsNotNone(frame)
        self.assertEqual(frame.property("width"), app.FIXED_SEARCH_WINDOW_WIDTH)

    def test_window_leaves_room_for_the_shadow_on_every_side(self):
        import app
        self.host.load()
        window = self.host.window
        margin = window.property("shadowMargin")
        self.assertGreater(margin, 0)
        self.assertEqual(window.width(), app.FIXED_SEARCH_WINDOW_WIDTH + 2 * margin)
        frame = window.findChild(QtCore.QObject, "shellFrame")
        self.assertEqual((frame.property("x"), frame.property("y")), (margin, margin))


class ShadowTests(unittest.TestCase):
    """The shadow used to come from DWM, which draws it around the whole native window
    at once -- an empty box with a shadow while the QML content was still fading in.
    It must live in QML, inside the layer that fades."""

    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(
            self.vm, self.apply, self.services.translate, animations_enabled=False,
        )
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def test_shadow_sits_inside_the_fading_frame(self):
        from PySide6 import QtQuick
        shadow = self.root.findChild(QtQuick.QQuickItem, "shellShadow")
        self.assertIsNotNone(shadow)
        self.assertEqual(shadow.parentItem().objectName(), "shellFrame")

    def test_frame_is_invisible_until_open_and_opaque_after(self):
        frame = self.root.findChild(QtCore.QObject, "shellFrame")
        self.assertEqual(frame.property("opacity"), 0)
        self.root.show()
        self.root.playOpen()
        self.app.processEvents()
        self.assertEqual(frame.property("opacity"), 1)



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

class SearchAndCategoryTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        # animations off: Behaviors would leave colours mid-transition when we read them.
        self.host = QmlPaletteHost(
            self.vm, self.apply, self.services.translate, animations_enabled=False,
        )
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def _child(self, name):
        return self.root.findChild(QtCore.QObject, name)

    def test_animations_can_be_disabled(self):
        self.assertFalse(self.root.property("themeProbe").property("animationsEnabled"))

    def test_search_field_exists(self):
        self.assertIsNotNone(self._child("searchField"))

    def test_typing_in_qml_updates_the_view_model(self):
        self._child("searchField").setProperty("text", "gaussian")
        self.app.processEvents()
        self.assertEqual(self.vm.query, "gaussian")
        self.assertEqual(self.vm.results.rowCount(), 2)

    def test_category_bar_reflects_the_active_category(self):
        bar = self._child("categoryBar")
        self.assertIsNotNone(bar)
        self.vm.select_category("Audio")
        self.app.processEvents()
        self.assertEqual(bar.property("activeCategory"), "Audio")

    def test_todos_is_shown_when_no_category_is_active(self):
        bar = self._child("categoryBar")
        self.vm.select_category("Todos")
        self.app.processEvents()
        self.assertEqual(bar.property("activeCategory"), "Todos")

    def test_connection_dot_colour_tracks_the_state(self):
        dot = self._child("connectionDot")
        self.assertIsNotNone(dot)
        self.vm.set_connection_state("connected")
        self.app.processEvents()
        connected = dot.property("color")
        self.vm.set_connection_state("offline")
        self.app.processEvents()
        self.assertNotEqual(connected, dot.property("color"))


class ResultListTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(
            self.vm, self.apply, self.services.translate, animations_enabled=False,
        )
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def _child(self, name):
        return self.root.findChild(QtCore.QObject, name)

    def test_list_exists(self):
        self.assertIsNotNone(self._child("resultList"))

    def test_list_count_follows_the_model(self):
        self.vm.set_query("gaussian")
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("count"), 2)

    def test_list_empties_when_nothing_matches(self):
        self.vm.set_query("nothing matches this")
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("count"), 0)

    def test_current_index_follows_the_view_model(self):
        self.vm.set_query("gaussian")
        self.vm.move_selection(1)
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("currentIndex"), 1)

    def test_selection_survives_a_new_query(self):
        self.vm.set_query("gaussian")
        self.vm.move_selection(1)
        self.vm.set_query("studio")
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("currentIndex"), 0)

    def test_rows_render_the_model_text(self):
        # count alone passed while every row rendered blank: the delegate's
        # `model.title` bindings evaluated to undefined. Read what a row shows.
        import time
        self.root.show()
        self.root.playOpen()
        self.vm.set_query("gaussian")
        end = time.time() + 0.5
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)
        content = self._child("resultList").property("contentItem")
        rows = sorted(
            (item for item in content.childItems() if item.property("title") is not None),
            key=lambda item: item.y(),
        )
        model = self.vm.results
        expected = [model.data(model.index(i, 0), model.TitleRole) for i in range(model.rowCount())]
        self.assertEqual([row.property("title") for row in rows], expected)
        self.assertTrue(all(row.property("typeLabel") for row in rows))
        self.assertTrue(rows[0].property("selected"))
        self.assertEqual(self.host.warnings, [])

    def test_all_rows_are_present_without_chunked_rendering(self):
        # The widget palette rendered 16 rows then chunked the rest via a timer.
        # A ListView is virtualised, so count is exact immediately.
        self.services.catalog = [{"name": f"Blur {i}", "type": "effect_video"} for i in range(200)]
        self.vm.set_query("blur")
        self.app.processEvents()
        self.assertEqual(self._child("resultList").property("count"), 200)


class FooterTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(
            self.vm, self.apply, self.services.translate, animations_enabled=False,
        )
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def _child(self, name):
        return self.root.findChild(QtCore.QObject, name)

    def test_footer_exists(self):
        self.assertIsNotNone(self._child("footer"))

    def test_status_text_follows_the_view_model(self):
        self.vm.set_query("gaussian")
        self.app.processEvents()
        self.assertEqual(self._child("footer").property("status"), self.vm.statusText)
        self.assertEqual(self._child("footer").property("status"), "2/2")

    def test_hint_follows_the_view_model(self):
        self.assertEqual(self._child("footer").property("hint"), self.vm.footerHint)

    def test_busy_follows_the_apply_controller(self):
        footer = self._child("footer")
        self.assertFalse(footer.property("busy"))
        self.apply.begin({"name": "X"})
        self.app.processEvents()
        self.assertTrue(footer.property("busy"))
        self.apply.complete("error")
        self.app.processEvents()
        self.assertFalse(footer.property("busy"))

    def test_apply_phase_follows_the_controller(self):
        footer = self._child("footer")
        self.apply.begin({"name": "X"})
        self.app.processEvents()
        self.assertEqual(footer.property("applyPhase"), "busy")
        self.apply.complete("ok")
        self.app.processEvents()
        self.assertEqual(footer.property("applyPhase"), "success")

    def test_empty_state_shows_only_in_message_state(self):
        empty = self._child("emptyState")
        self.assertIsNotNone(empty)
        self.vm.set_query("gaussian")
        self.app.processEvents()
        self.assertFalse(empty.property("visible"))
        self.vm.set_query("nothing matches this")
        self.app.processEvents()
        self.assertTrue(empty.property("visible"))


class NestPanelTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(
            self.vm, self.apply, self.services.translate, animations_enabled=False,
        )
        self.host.load()
        self.root = self.host.window

    def tearDown(self):
        self.host.shutdown()

    def _child(self, name):
        return self.root.findChild(QtCore.QObject, name)

    def test_panel_starts_closed(self):
        panel = self._child("nestPanel")
        self.assertIsNotNone(panel)
        self.assertFalse(panel.property("open"))

    def test_open_nest_panel_opens_it(self):
        self.root.openNestPanel()
        self.app.processEvents()
        self.assertTrue(self._child("nestPanel").property("open"))
        self.assertTrue(self.root.property("nestPanelOpen"))

    def test_close_nest_panel_closes_it(self):
        self.root.openNestPanel()
        self.root.closeNestPanel()
        self.app.processEvents()
        self.assertFalse(self._child("nestPanel").property("open"))

    def test_confirming_emits_the_typed_name(self):
        seen = []
        self.root.nestConfirmed.connect(seen.append)
        self.root.openNestPanel()
        self._child("nestPanel").setProperty("nestName", "My Nest")
        self._child("nestPanel").confirm()
        self.app.processEvents()
        self.assertEqual(seen, ["My Nest"])

    def test_cancelling_emits_and_closes(self):
        seen = []
        self.root.nestCancelled.connect(lambda: seen.append(True))
        self.root.openNestPanel()
        self._child("nestPanel").cancel()
        self.app.processEvents()
        self.assertEqual(seen, [True])
        self.assertFalse(self._child("nestPanel").property("open"))

    def test_footer_hint_switches_while_the_panel_is_open(self):
        footer = self._child("footer")
        normal = footer.property("hint")
        self.root.openNestPanel()
        self.app.processEvents()
        self.assertNotEqual(footer.property("hint"), normal)
        self.root.closeNestPanel()
        self.app.processEvents()
        self.assertEqual(footer.property("hint"), normal)


class OpenCloseMotionTests(unittest.TestCase):
    def _host(self, *, animations):
        self.app = qt_app()
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(
            self.vm, self.apply, self.services.translate, animations_enabled=animations,
        )
        self.host.load()
        self.root = self.host.window
        # QQuickWindow drives QML animations and layout from its render loop, which
        # only runs once the window is shown -- even offscreen.
        self.root.show()
        self.app.processEvents()
        return self.root

    def _settle(self, seconds=0.8):
        """Layout and animations need real elapsed time, not just event-loop turns."""
        import time
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def tearDown(self):
        self.host.shutdown()

    def test_play_open_marks_the_shell_visible(self):
        root = self._host(animations=True)
        root.playOpen()
        self.app.processEvents()
        self.assertTrue(root.property("shellVisible"))

    def test_play_close_clears_it(self):
        root = self._host(animations=True)
        root.playOpen()
        root.playClose()
        self.app.processEvents()
        self.assertFalse(root.property("shellVisible"))

    def test_close_finished_fires_with_animations_on(self):
        import time
        root = self._host(animations=True)
        seen = []
        root.closeFinished.connect(lambda: seen.append(True))
        root.playOpen()
        self.app.processEvents()
        root.playClose()
        deadline = time.time() + 3.0
        while time.time() < deadline and not seen:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(seen, [True])

    def test_close_finished_fires_with_animations_off(self):
        # Behaviors are skipped entirely when animations are disabled, so the signal
        # must still fire or hide() would never complete.
        root = self._host(animations=False)
        seen = []
        root.closeFinished.connect(lambda: seen.append(True))
        root.playOpen()
        self.app.processEvents()
        root.playClose()
        self.app.processEvents()
        self.assertEqual(seen, [True])

    def test_window_height_tracks_the_content(self):
        root = self._host(animations=False)
        self.vm.set_query("nothing matches this")
        self._settle()
        message_height = root.height()
        self.vm.set_query("gaussian")
        self._settle()
        self.assertGreater(root.height(), message_height)


if __name__ == "__main__":
    unittest.main()
