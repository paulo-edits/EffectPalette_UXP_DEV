"""Boots the real QtEffectPalette offscreen and drives it end to end.

The other suites test the view-models and the QML in isolation, which means a method
deleted during the QML rewrite but still *called* from __init__ or a loader callback
slips through all of them. This suite constructs the real thing and exercises the paths
those callbacks take.

The execution adapter is stubbed so this never talks to a running Premiere.
"""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app


class StubAdapter:
    backend_name = "stub"

    def __init__(self):
        self.sent = []

    def is_success(self, status):
        return status == "done"

    def is_terminal(self, status):
        return status == "done" or bool(status and str(status).startswith("error"))

    def poll_status(self, timestamp):
        return None

    def diagnostics(self):
        return {"backend": self.backend_name}

    def stop(self):
        pass


class PaletteBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Never reach for the real port; a live plugin would otherwise connect here.
        cls._real_factory = app.create_execution_adapter
        app.create_execution_adapter = lambda *a, **k: StubAdapter()
        cls.palette = app.QtEffectPalette()

    @classmethod
    def tearDownClass(cls):
        cls.palette.shutdown()
        app.create_execution_adapter = cls._real_factory

    def _settle(self, seconds=0.4):
        end = time.time() + seconds
        while time.time() < end:
            self.palette.app.processEvents()
            time.sleep(0.005)

    def test_it_builds_a_quick_window(self):
        self.assertIsNotNone(self.palette.window)
        self.assertIsNotNone(self.palette.qml_host)
        self.assertEqual(self.palette.qml_host.warnings, [])

    def test_the_window_controller_is_wired(self):
        self.assertIsNotNone(self.palette.window_controller)
        self.assertIsInstance(self.palette._window_hwnd(), int)

    def test_loader_snapshot_callback_does_not_raise(self):
        # This is the callback that kept a reference to a deleted widget method.
        self.palette._on_loader_snapshot_ready(self.palette.loader.snapshot)
        self._settle(0.1)

    def test_open_search_and_close(self):
        self.palette.show()
        self._settle()
        self.assertTrue(self.palette.is_open)
        self.assertTrue(self.palette.window.property("shellVisible"))

        self.palette.window.setProperty("searchText", "blur")
        self._settle()
        self.assertGreater(self.palette.view_model.results.rowCount(), 0)
        self.assertTrue(self.palette.view_model.statusText)

        self.palette.hide()
        self._settle()
        self.assertFalse(self.palette.is_open)
        self.assertFalse(self.palette.window.property("shellVisible"))

    @unittest.skipUnless(hasattr(__import__("ctypes"), "WinDLL"), "Windows only")
    def test_show_does_not_ask_dwm_for_a_native_frame(self):
        # A DWM frame (rounded corners, border, shadow) spans the whole native window and
        # appears at once, while the QML content fades in: an empty box on every open.
        import ctypes
        from unittest import mock

        loaded = []
        real_windll = ctypes.WinDLL

        def spy(name, *args, **kwargs):
            loaded.append(str(name).lower())
            return real_windll(name, *args, **kwargs)

        with mock.patch.object(ctypes, "WinDLL", side_effect=spy):
            self.palette.show()
            self._settle(0.2)
            self.palette.hide()
            self._settle()
        self.assertNotIn("dwmapi", loaded)

    def test_margin_pass_through_runs_only_while_open_and_focused(self):
        self.palette.show()
        self._settle(0.1)
        self.palette._report_focus_acquired(0)
        self.assertTrue(self.palette.margin_pass_through.running)
        self.palette.hide()
        self._settle()
        self.assertFalse(self.palette.margin_pass_through.running)

    def _find_item(self, name):
        def walk(item):
            for child in item.childItems():
                if child.objectName() == name:
                    return child
                found = walk(child)
                if found is not None:
                    return found
            return None
        return walk(self.palette.window.contentItem())

    def test_language_switch_retranslates_the_open_palette(self):
        # apply_language switches in memory only; it must never write settings.json.
        from unittest import mock
        original = app.CURRENT_LANGUAGE
        other = "pt" if original != "pt" else "en"
        with mock.patch.object(app, "_save_language") as save:
            try:
                app.apply_language(other)
                self.palette.on_language_changed()
                self._settle(0.2)
                self.assertEqual(self._find_item("searchPlaceholder").property("text"),
                                 app.tr("search_placeholder"))
                self.assertEqual(app.CURRENT_LANGUAGE, other)
            finally:
                app.apply_language(original)
                self.palette.on_language_changed()
                self._settle(0.2)
            save.assert_not_called()

    def test_selection_moves(self):
        self.palette.window.setProperty("searchText", "blur")
        self._settle()
        self.palette._move_selection(1)
        self.assertEqual(self.palette.view_model.selectedIndex, 1)
        self.assertIsNotNone(self.palette._selected_payload())

    def test_nest_panel_round_trip(self):
        self.palette._show_nest_options({"name": "Nest", "type": "timeline_action"})
        self._settle(0.2)
        self.assertTrue(self.palette.window.property("nestPanelOpen"))
        self.palette._close_nest_options(restore=False)
        self._settle(0.2)
        self.assertFalse(self.palette.window.property("nestPanelOpen"))

    def test_apply_lifecycle_drives_the_status(self):
        self.palette.apply_controller.begin({"name": "Gaussian Blur", "type": "effect_video"})
        self._settle(0.1)
        self.assertTrue(self.palette.apply_controller.busy)
        self.palette._complete_apply("error:NO_SELECTION")
        self._settle(0.1)
        self.assertEqual(self.palette.apply_controller.state, "error")
        self.assertTrue(self.palette.view_model.statusText)

    def test_manual_refresh_does_not_raise(self):
        self.palette._manual_refresh()
        self._settle(0.1)


if __name__ == "__main__":
    unittest.main()
