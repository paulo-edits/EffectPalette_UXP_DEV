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
        # y is covered by the motion tests: a hidden shell sits lower, ready to slide up.
        self.assertEqual(frame.property("x"), margin)


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

    def test_strip_is_hidden_without_a_message(self):
        # The key hints and the result count are gone: the footer is a status strip
        # that appears only when an apply has something to say.
        self.vm.set_query("gaussian")
        self.app.processEvents()
        footer = self._child("footer")
        self.assertFalse(footer.property("shown"))
        self.assertEqual(footer.property("height"), 0)

    def test_strip_shows_an_apply_message(self):
        self.vm.set_status_override("Applying: Gaussian Blur")
        self.app.processEvents()
        footer = self._child("footer")
        self.assertTrue(footer.property("shown"))
        self.assertGreater(footer.property("height"), 0)
        self.assertEqual(footer.property("status"), "Applying: Gaussian Blur")

    def test_strip_hides_after_the_next_search(self):
        self.vm.set_status_override("Select a clip first.")
        self.vm.set_query("gaussian")
        self.app.processEvents()
        self.assertFalse(self._child("footer").property("shown"))

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

    def test_hidden_shell_sits_lower_and_slides_up_on_open(self):
        root = self._host(animations=False)
        frame = root.findChild(QtCore.QObject, "shellFrame")
        margin = root.property("shadowMargin")
        slide = root.property("themeProbe").property("slideDistance")
        self.assertGreater(slide, 0)
        self.assertEqual(frame.property("y"), margin + slide)
        root.playOpen()
        self.app.processEvents()
        self.assertEqual(frame.property("y"), margin)
        root.playClose()
        self.app.processEvents()
        self.assertEqual(frame.property("y"), margin + slide)

    def test_there_is_no_scale_pop(self):
        root = self._host(animations=False)
        frame = root.findChild(QtCore.QObject, "shellFrame")
        self.assertEqual(frame.property("scale"), 1)
        root.playOpen()
        self.app.processEvents()
        self.assertEqual(frame.property("scale"), 1)

    def _slide_progress_at(self, fraction, *, opening):
        """How far the frame has travelled `fraction` of the way through the motion."""
        import time
        import app
        root = self._host(animations=True)
        frame = root.findChild(QtCore.QObject, "shellFrame")
        margin = root.property("shadowMargin")
        slide = root.property("themeProbe").property("slideDistance")
        duration = app.OPEN_ANIMATION_MS / 1000.0
        if not opening:
            root.playOpen()
            self._settle(duration + 0.3)
        start = time.perf_counter()
        if opening:
            root.playOpen()
        else:
            root.playClose()
        while time.perf_counter() - start < duration * fraction:
            self.app.processEvents()
            time.sleep(0.002)
        offset = frame.property("y") - margin  # slide -> 0 while opening, 0 -> slide closing
        travelled = (slide - offset) if opening else offset
        return travelled / slide

    def test_open_slides_in_fast_then_settles(self):
        # The slide took its easing from shellVisible, but the Behavior started before
        # that binding updated: opening ran the closing curve -- a slow start, then a
        # snap into place after the fade had already finished.
        self.assertGreater(self._slide_progress_at(0.4, opening=True), 0.5)

    def test_close_starts_gently_then_drops_away(self):
        self.assertLess(self._slide_progress_at(0.4, opening=False), 0.5)

    def test_window_height_tracks_the_content(self):
        root = self._host(animations=False)
        self.vm.set_query("nothing matches this")
        self._settle()
        message_height = root.height()
        self.vm.set_query("gaussian")
        self._settle()
        self.assertGreater(root.height(), message_height)


class LayoutTests(unittest.TestCase):
    """The spacing pass: one 8 px grid, one shared right edge, readable hints."""

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
        self.root.show()
        self.root.playOpen()
        self.vm.set_query("")
        self._settle()

    def tearDown(self):
        self.host.shutdown()

    def _settle(self, seconds=0.3):
        import time
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def _all_items(self, item=None):
        # Repeater delegates are reachable through the visual tree, not findChild.
        item = item or self.root.contentItem()
        for child in item.childItems():
            yield child
            yield from self._all_items(child)

    def _named(self, name):
        return [item for item in self._all_items() if item.objectName() == name]

    def _item(self, name):
        found = self._named(name)
        self.assertEqual(len(found), 1, f"expected exactly one {name!r}")
        return found[0]

    def _center_x(self, item):
        if isinstance(item, str):
            item = self._item(item)
        return item.mapToItem(self._item("shellFrame"), QtCore.QPointF(item.width() / 2, 0)).x()

    def _theme(self, name):
        return self.root.property("themeProbe").property(name)

    def test_connection_dot_is_centred_under_the_refresh_button(self):
        # One right-hand column: the refresh icon above, the connection dot below it.
        self.assertEqual(round(self._center_x("refreshButton")),
                         round(self._center_x("connectionDot")))

    def test_search_prompt_is_an_icon(self):
        self.assertIn("Canvas", self._item("searchIcon").metaObject().className())

    def test_refresh_icon_is_borderless_and_muted(self):
        # QQmlProperty reads the grouped border property that item.property() cannot convert.
        from PySide6 import QtQml
        self.assertEqual(QtQml.QQmlProperty(self._item("refreshButton"), "border.width").read(), 0)
        self.assertEqual(self._item("refreshGlyph").property("color"), self._theme("textMuted"))

    def test_selection_highlight_is_inset_like_the_rows(self):
        # The highlight carried anchor margins without anchors, so it ran edge to edge
        # while the rows sat inset.
        self.root.setProperty("searchText", "gaussian")
        self._settle()
        result_list = self._item("resultList")
        highlight = self._item("selectionHighlight")
        left = highlight.mapToItem(result_list, QtCore.QPointF(0, 0)).x()
        inset = self._theme("spaceSm")
        self.assertEqual(left, inset)
        self.assertEqual(highlight.width(), result_list.width() - 2 * inset)

    def test_bands_sit_on_the_8px_grid(self):
        for name in ("searchField", "categoryBar", "footer"):
            height = self._item(name).height()
            self.assertEqual(height % 8, 0, f"{name} is {height} px tall")

    def test_search_input_uses_the_input_size(self):
        size = self.root.property("themeProbe").property("sizeInput")
        self.assertGreaterEqual(size, 18)
        self.assertEqual(self._item("searchInput").property("font").pixelSize(), size)

    def test_placeholder_shows_only_while_empty(self):
        placeholder = self._item("searchPlaceholder")
        self.assertTrue(placeholder.isVisible())
        self.root.setProperty("searchText", "blur")
        self._settle(0.05)
        self.assertFalse(placeholder.isVisible())
        self.root.setProperty("searchText", "")
        self._settle(0.05)
        self.assertTrue(placeholder.isVisible())

    def test_no_key_hints_are_shown(self):
        self.assertEqual(self._named("footerKeycap"), [])
        self.assertEqual(self._named("footerPlainHint"), [])

    def test_selection_highlight_takes_the_selected_rows_colour(self):
        from test_view_models import make_row
        self.services.build_row_model = lambda p: make_row(
            p["name"], accent=p.get("accent", "video"), payload=p)
        self.services.catalog = [
            {"name": "Blur Video", "type": "effect_video", "accent": "video"},
            {"name": "Blur Audio", "type": "effect_audio", "accent": "audio"},
        ]
        self.root.setProperty("searchText", "blur")
        self._settle()
        highlight = self._item("selectionHighlight")
        result_list = self._item("resultList")
        seen = []
        for index in (0, 1):
            self.vm.set_selected_index(index)
            self._settle(0.05)
            row_accent = result_list.property("currentItem").property("accent")
            self.assertEqual(highlight.property("accent"), row_accent)
            seen.append(row_accent)
        self.assertNotEqual(seen[0], seen[1], "the two rows should have different colours")

    def test_active_pill_is_drawn_on_the_first_render(self):
        # The pill found its chip through Repeater.itemAt(), which QML does not re-run
        # once the chips exist -- so it stayed invisible until the category changed.
        pill = self._item("activePill")
        chip = self._item("chip_Todos")
        self.assertTrue(pill.isVisible())
        self.assertGreater(pill.width(), 0)
        # Tabs: a thin underline centred under the active label, not a chip-sized pill.
        self.assertLessEqual(pill.height(), 3)
        self.assertEqual(round(self._center_x(pill)), round(self._center_x(chip)))

    def test_row_icons_follow_the_icon_kind(self):
        # A drawn line icon per type replaced the unicode glyph in a tinted tile; an
        # unknown kind falls back to the effect icon.
        import dataclasses
        from test_view_models import make_row
        self.services.build_row_model = lambda p: dataclasses.replace(
            make_row(p["name"], payload=p), icon_kind=p["kind"])
        self.services.catalog = [
            {"name": "Row fx", "type": "effect_video", "kind": "fx"},
            {"name": "Row audio", "type": "effect_video", "kind": "audio"},
            {"name": "Row transition", "type": "effect_video", "kind": "transition"},
            {"name": "Row unknown", "type": "effect_video", "kind": "something-new"},
        ]
        self.root.setProperty("searchText", "row")
        self._settle()
        icons = sorted(self._named("rowIcon"), key=lambda item: item.mapToScene(QtCore.QPointF(0, 0)).y())
        self.assertEqual([icon.property("glyph") for icon in icons],
                         ["fx", "audio", "transition", "fx"])
        self.assertIn("Canvas", icons[0].metaObject().className())

    def test_list_has_inner_padding(self):
        # Breathing room above the first row and below the last. Below the last row it
        # is list margin while the status strip shows, and plain palette otherwise.
        self.root.setProperty("searchText", "gaussian")
        self._settle()
        result_list = self._item("resultList")
        inset = self._theme("spaceSm")
        self.assertEqual(result_list.property("topMargin"), inset)
        gap = result_list.property("bottomMargin") + self._item("listBottomSpacer").height()
        self.assertEqual(gap, inset)

    def _shell_bottom_gap(self, item):
        frame = self._item("shellFrame")
        bottom = item.mapToItem(frame, QtCore.QPointF(0, item.height())).y()
        return frame.height() - bottom

    def test_list_stays_clear_of_the_rounded_bottom_corners(self):
        # The shell clips to a rectangle, not its rounded shape: a list reaching the
        # bottom edge painted rows and the fade over the corners and the border.
        self.root.setProperty("searchText", "gaussian")
        self._settle()
        self.assertGreaterEqual(self._shell_bottom_gap(self._item("resultList")),
                                self._theme("spaceSm"))

    def test_idle_tabs_stay_clear_of_the_rounded_bottom_corners(self):
        # With no results the tabs were the last band, so the underline under "All"
        # sat on the bottom edge, inside the rounded corner.
        self.assertGreaterEqual(self._shell_bottom_gap(self._item("activePill")),
                                self._theme("spaceSm"))

    def test_fades_stay_inside_the_side_borders(self):
        result_list = self._item("resultList")
        for name in ("listTopFade", "listBottomFade"):
            fade = self._item(name)
            left = fade.mapToItem(result_list, QtCore.QPointF(0, 0)).x()
            self.assertGreaterEqual(left, 1, name)
            self.assertLessEqual(left + fade.width(), result_list.width() - 1, name)

    def _thirty_rows(self):
        from models import PaletteLayoutMetrics
        self.services.catalog = [{"name": f"Blur {i}", "type": "effect_video"} for i in range(30)]
        self.root.setProperty("searchText", "blur")
        self._settle()
        return self._item("resultList"), PaletteLayoutMetrics().row_height

    def test_selection_keeps_the_next_row_in_view(self):
        # Moving down, the list scrolled only enough to keep the selection at the very
        # bottom, so it looked like the last item however many rows followed.
        result_list, row = self._thirty_rows()
        for index in range(1, 12):
            self.vm.move_selection(1)
            self._settle(0.05)
            view_bottom = result_list.property("contentY") + result_list.height()
            next_top = (index + 1) * row
            self.assertLessEqual(next_top + row / 2, view_bottom, f"row {index + 1} hidden")

    def test_last_row_sits_fully_in_view_at_the_end(self):
        result_list, row = self._thirty_rows()
        self.vm.set_selected_index(29)
        self._settle()
        view_bottom = result_list.property("contentY") + result_list.height()
        self.assertLessEqual(30 * row, view_bottom)

    def test_edge_fades_show_only_where_more_rows_continue(self):
        # A fade over the very first or last row made the end of the list look cut
        # off; it belongs only where rows continue past that edge.
        top, bottom = self._item("listTopFade"), self._item("listBottomFade")
        self.root.setProperty("searchText", "gaussian")  # two rows: nothing scrolls
        self._settle()
        self.assertEqual((top.property("opacity"), bottom.property("opacity")), (0, 0))

        self._thirty_rows()
        self.assertEqual((top.property("opacity"), bottom.property("opacity")), (0, 1))
        self.vm.set_selected_index(15)
        self._settle()
        self.assertEqual((top.property("opacity"), bottom.property("opacity")), (1, 1))
        self.vm.set_selected_index(29)
        self._settle()
        self.assertEqual((top.property("opacity"), bottom.property("opacity")), (1, 0))

    def test_every_filter_the_palette_can_switch_to_has_a_tab(self):
        # /trans switched to Transitions, which had no tab: an invisible filter.
        import app
        for key in ["Todos", *app.CATEGORY_TYPE_FILTERS]:
            self.assertEqual(len(self._named(f"chip_{key}")), 1, key)

    def test_transitions_tab_shows_the_active_filter(self):
        self.vm.select_category("Transicoes")
        self._settle(0.05)
        pill = self._item("activePill")
        self.assertTrue(pill.isVisible())
        self.assertEqual(round(self._center_x(pill)), round(self._center_x("chip_Transicoes")))

    def test_selection_highlight_is_vivid_for_every_item_colour(self):
        # Pale pastels (the preset lavender) blended to near-grey at a fixed alpha.
        # Every item colour must give a clearly tinted highlight, in its own hue.
        import colorsys
        import dataclasses
        from PySide6 import QtGui
        from test_view_models import make_row
        kinds = ["Video", "Audio", "Presets", "Projeto", "Favoritos", "Transicoes"]
        self.services.build_row_model = lambda p: dataclasses.replace(
            make_row(p["name"], payload=p), accent_kind=p["kind"])
        self.services.catalog = [{"name": f"Row {k}", "type": "effect_video", "kind": k} for k in kinds]
        self.root.setProperty("searchText", "row")
        self._settle()
        surface = QtGui.QColor(self._theme("surface"))
        highlight = self._item("selectionHighlight")
        result_list = self._item("resultList")
        for index, kind in enumerate(kinds):
            self.vm.set_selected_index(index)
            self._settle(0.05)
            accent = QtGui.QColor(result_list.property("currentItem").property("accent"))
            fill = QtGui.QColor(highlight.property("color"))
            a = fill.alphaF()
            blended = [a * f + (1 - a) * s for f, s in
                       ((fill.redF(), surface.redF()), (fill.greenF(), surface.greenF()),
                        (fill.blueF(), surface.blueF()))]
            hue, saturation, _value = colorsys.rgb_to_hsv(*blended)
            self.assertGreaterEqual(saturation, 0.3, f"{kind} highlight reads grey")
            accent_hue = accent.hsvHueF()
            distance = min(abs(hue - accent_hue), 1 - abs(hue - accent_hue)) * 360
            self.assertLess(distance, 20, f"{kind} highlight left its item's hue")

    def test_underline_sits_exactly_under_the_label(self):
        # Spanning the whole tab, it stuck out past the "All" text into the tab's
        # padding, out of line with the text and the search icon above it.
        pill = self._item("activePill")
        label = next(item for item in self._named("chipLabel")
                     if item.parentItem().objectName() == "chip_Todos")
        frame = self._item("shellFrame")
        pill_left = pill.mapToItem(frame, QtCore.QPointF(0, 0)).x()
        label_left = label.mapToItem(frame, QtCore.QPointF(0, 0)).x()
        self.assertEqual(round(pill_left), round(label_left))
        self.assertEqual(round(pill.width()), round(label.width()))

    def test_list_edges_fade_while_scrolling(self):
        # Padding only moves the first and last rows; while scrolling, rows would still
        # slide flush against the tabs and the border without a fade at each edge.
        inset = self._theme("spaceSm")
        for name in ("listTopFade", "listBottomFade"):
            self.assertGreaterEqual(self._item(name).height(), inset, name)

    def test_active_chip_keeps_its_width(self):
        chip = self._item("chip_Todos")
        width = chip.width()
        self.vm.select_category("Video")
        self._settle(0.05)
        self.assertEqual(chip.width(), width)


class KeyboardCategoryTests(unittest.TestCase):
    """Tab / Shift+Tab in the search field switch category, wrapping around."""

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
        self.root.show()
        self.root.playOpen()
        self.root.focusSearch()
        self._settle()

    def tearDown(self):
        self.host.shutdown()

    def _settle(self, seconds=0.1):
        import time
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def _press(self, key, modifier=QtCore.Qt.KeyboardModifier.NoModifier):
        from PySide6 import QtTest
        QtTest.QTest.keyClick(self.root, key, modifier)
        self._settle(0.05)

    def test_tab_moves_to_the_next_category(self):
        self.root.setProperty("searchText", "gaussian")
        self._settle(0.05)
        self._press(QtCore.Qt.Key.Key_Tab)
        self.assertEqual(self.vm.activeCategory, "Video")
        self._press(QtCore.Qt.Key.Key_Tab)
        self.assertEqual(self.vm.activeCategory, "Audio")
        # The search itself is untouched, and the caret stays in the field.
        self.assertEqual(self.root.property("searchText"), "gaussian")
        self.assertTrue(self.root.property("searchHasFocus"))

    def test_shift_tab_wraps_from_all_to_favorites(self):
        # Windows delivers Shift+Tab as Key_Backtab.
        self._press(QtCore.Qt.Key.Key_Backtab, QtCore.Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.vm.activeCategory, "Favoritos")
        self.assertTrue(self.root.property("searchHasFocus"))

    def test_tab_wraps_from_the_last_category_back_to_all(self):
        self.vm.select_category("Favoritos")
        self._settle(0.05)
        self._press(QtCore.Qt.Key.Key_Tab)
        self.assertIsNone(self.vm.activeCategory)  # "Todos": no filter


class LanguageTests(unittest.TestCase):
    """The palette follows the language picked in the tray, immediately."""

    STRINGS = {
        "en": {"search_placeholder": "Search", "no_results_helper": "No results"},
        "pt": {"search_placeholder": "Buscar", "no_results_helper": "Nenhum resultado"},
    }
    CATEGORIES = {"en": {"Todos": "All"}, "pt": {"Todos": "Todos"}}

    def setUp(self):
        self.app = qt_app()
        self.lang = "en"
        self.services = FakeQueryServices(CATALOG)
        self.vm = PaletteViewModel(self.services)
        self.apply = ApplyController(FakeAdapter(), FakeScheduler())
        self.host = QmlPaletteHost(
            self.vm, self.apply,
            lambda key, **kw: self.STRINGS[self.lang].get(key, key),
            category_label=lambda key: self.CATEGORIES[self.lang].get(key, key),
            animations_enabled=False,
        )
        self.host.load()
        self.root = self.host.window
        self.root.show()
        self.app.processEvents()

    def tearDown(self):
        self.host.shutdown()

    def _find(self, name):
        def walk(item):
            for child in item.childItems():
                if child.objectName() == name:
                    return child
                found = walk(child)
                if found is not None:
                    return found
            return None
        return walk(self.root.contentItem())

    def test_loads_without_warnings(self):
        self.assertEqual(self.host.warnings, [])

    def test_tabs_show_translated_labels_but_keep_their_keys(self):
        self.assertEqual(self._find("chip_Todos").property("label"), "All")

    def test_retranslate_updates_the_open_palette(self):
        self.lang = "pt"
        self.host.retranslate()
        self.app.processEvents()
        self.assertEqual(self._find("searchPlaceholder").property("text"), "Buscar")
        self.assertEqual(self._find("chip_Todos").property("label"), "Todos")


if __name__ == "__main__":
    unittest.main()
