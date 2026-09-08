"""Headless tests for the native window layer, driven through a fake adapter.

These prove the retry and focus bookkeeping without a display. They prove nothing about
Premiere - that needs a real host test.
"""

from __future__ import annotations

import unittest

from window_control import PaletteWindowController


class FakeWindowAdapter:
    def __init__(self, *, focus_after: int = 0, handle: int = 4242):
        self.shown = 0
        self.raised = 0
        self.activated = 0
        self.focus_requests = 0
        self.moved_to = None
        self._handle = handle
        self._focus_after = focus_after

    def show(self):
        self.shown += 1

    def raise_window(self):
        self.raised += 1

    def activate(self):
        self.activated += 1

    def handle(self):
        return self._handle

    def move(self, x, y):
        self.moved_to = (x, y)

    def width(self):
        return 700

    def height_hint(self):
        return 320

    def focus_input(self):
        self.focus_requests += 1

    def has_input_focus(self):
        return self.focus_requests > self._focus_after


class FakeScheduler:
    def __init__(self):
        self.pending = []
        self.cancelled = []
        self.next_id = 1

    def after(self, delay_ms, callback):
        job_id = self.next_id
        self.next_id += 1
        self.pending.append((job_id, callback))
        return job_id

    def after_cancel(self, job_id):
        self.cancelled.append(job_id)
        self.pending = [(i, c) for i, c in self.pending if i != job_id]

    def drain(self, limit=10):
        for _ in range(limit):
            if not self.pending:
                return
            _job_id, callback = self.pending.pop(0)
            callback()


class FakeNative:
    def __init__(self, foreground=9999):
        self.foreground = foreground
        self.activated = []

    def foreground_handle(self):
        return self.foreground

    def activate_handle(self, handle):
        self.activated.append(handle)


class FocusAcquisitionTests(unittest.TestCase):
    def _controller(self, **kwargs):
        self.adapter = FakeWindowAdapter(**kwargs)
        self.scheduler = FakeScheduler()
        self.native = FakeNative()
        controller = PaletteWindowController(self.adapter, self.scheduler, self.native)
        controller.is_open = True
        return controller

    def test_first_attempt_that_wins_focus_schedules_no_retry(self):
        controller = self._controller(focus_after=0)
        controller.begin_focus_attempts(max_attempts=3)
        self.assertEqual(self.adapter.focus_requests, 1)
        self.assertEqual(self.scheduler.pending, [])

    def test_it_retries_until_focus_lands(self):
        controller = self._controller(focus_after=2)
        controller.begin_focus_attempts(max_attempts=3)
        self.scheduler.drain()
        self.assertEqual(self.adapter.focus_requests, 3)
        self.assertEqual(self.scheduler.pending, [])

    def test_it_gives_up_after_max_attempts(self):
        controller = self._controller(focus_after=99)
        controller.begin_focus_attempts(max_attempts=2)
        self.scheduler.drain()
        self.assertEqual(self.adapter.focus_requests, 3)  # initial + 2 retries
        self.assertEqual(self.scheduler.pending, [])

    def test_a_closed_palette_stops_retrying(self):
        controller = self._controller(focus_after=99)
        controller.begin_focus_attempts(max_attempts=3)
        controller.is_open = False
        self.scheduler.drain()
        self.assertEqual(self.adapter.focus_requests, 1)

    def test_each_attempt_shows_raises_and_activates(self):
        controller = self._controller(focus_after=0)
        controller.begin_focus_attempts(max_attempts=3)
        self.assertEqual(self.adapter.shown, 1)
        self.assertEqual(self.adapter.raised, 1)
        self.assertEqual(self.native.activated, [4242])

    def test_cancel_clears_the_pending_retry(self):
        controller = self._controller(focus_after=99)
        controller.begin_focus_attempts(max_attempts=3)
        controller.cancel_focus_attempts()
        self.assertEqual(self.scheduler.pending, [])

    def test_on_focus_acquired_fires_once_with_the_attempt_number(self):
        self.adapter = FakeWindowAdapter(focus_after=1)
        self.scheduler = FakeScheduler()
        self.native = FakeNative()
        seen = []
        controller = PaletteWindowController(
            self.adapter, self.scheduler, self.native, on_focus_acquired=seen.append,
        )
        controller.is_open = True
        controller.begin_focus_attempts(max_attempts=3)
        self.scheduler.drain()
        self.assertEqual(seen, [1])


class PreviousFocusTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeWindowAdapter()
        self.scheduler = FakeScheduler()
        self.native = FakeNative(foreground=9999)
        self.controller = PaletteWindowController(self.adapter, self.scheduler, self.native)

    def test_remember_then_restore_reactivates_the_previous_window(self):
        self.controller.remember_previous_focus()
        self.controller.restore_previous_focus()
        self.assertEqual(self.native.activated, [9999])

    def test_it_never_remembers_its_own_window(self):
        self.native.foreground = self.adapter.handle()
        self.controller.remember_previous_focus()
        self.controller.restore_previous_focus()
        self.assertEqual(self.native.activated, [])

    def test_restore_is_idempotent(self):
        self.controller.remember_previous_focus()
        self.controller.restore_previous_focus()
        self.controller.restore_previous_focus()
        self.assertEqual(self.native.activated, [9999])

    def test_handle_is_forwarded_from_the_adapter(self):
        self.assertEqual(self.controller.handle(), 4242)

    def test_previous_handle_is_readable_by_the_keystroke_paths(self):
        self.controller.remember_previous_focus()
        self.assertEqual(self.controller.previous_handle, 9999)

    def test_previous_handle_can_be_set_directly(self):
        # The recent-action path captures Premiere's handle itself when the palette
        # already owns focus.
        self.controller.previous_handle = 1234
        self.assertEqual(self.controller.previous_handle, 1234)
        self.controller.restore_previous_focus()
        self.assertEqual(self.native.activated, [1234])

    def test_restore_clears_the_previous_handle(self):
        self.controller.remember_previous_focus()
        self.controller.restore_previous_focus()
        self.assertIsNone(self.controller.previous_handle)


class AnchorTests(unittest.TestCase):
    def test_anchor_offsets_by_the_screen_origin(self):
        adapter = FakeWindowAdapter()
        controller = PaletteWindowController(adapter, FakeScheduler(), FakeNative())

        def chooser(**kwargs):
            return 100, 200

        controller.anchor_to_pointer(
            pointer=(500, 500),
            available=(1920, 0, 1920, 1080),  # x, y, width, height
            position_chooser=chooser,
        )
        self.assertEqual(adapter.moved_to, (2020, 200))

    def test_anchor_passes_the_window_size_to_the_chooser(self):
        adapter = FakeWindowAdapter()
        controller = PaletteWindowController(adapter, FakeScheduler(), FakeNative())
        seen = {}

        def chooser(**kwargs):
            seen.update(kwargs)
            return 0, 0

        controller.anchor_to_pointer(
            pointer=(640, 480),
            available=(0, 0, 1920, 1080),
            position_chooser=chooser,
        )
        self.assertEqual(seen["pointer_x"], 640)
        self.assertEqual(seen["pointer_y"], 480)
        self.assertEqual(seen["window_width"], 700)
        self.assertEqual(seen["window_height"], 320)
        self.assertEqual(seen["screen_width"], 1920)
        self.assertEqual(seen["screen_height"], 1080)


if __name__ == "__main__":
    unittest.main()


class QuickWindowAdapterTests(unittest.TestCase):
    """QQuickWindow speaks a different dialect than QWidget: requestActivate instead of
    activateWindow, setPosition instead of move. The adapter absorbs that."""

    def setUp(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        from palette_view_models import ApplyController, PaletteViewModel
        from qml_host import QmlPaletteHost
        from test_view_models import CATALOG, FakeAdapter, FakeQueryServices
        from test_view_models import FakeScheduler as VmScheduler

        services = FakeQueryServices(CATALOG)
        self.host = QmlPaletteHost(
            PaletteViewModel(services), ApplyController(FakeAdapter(), VmScheduler()),
            services.translate,
        )
        self.host.load()
        from window_control import QuickWindowAdapter
        self.adapter = QuickWindowAdapter(self.host.window)

    def tearDown(self):
        self.host.shutdown()

    def test_handle_is_a_real_window_id(self):
        self.adapter.show()
        self.assertIsInstance(self.adapter.handle(), int)
        self.assertNotEqual(self.adapter.handle(), 0)

    def test_handle_is_cached(self):
        self.adapter.show()
        self.assertEqual(self.adapter.handle(), self.adapter.handle())

    def test_move_sets_the_window_position(self):
        self.adapter.show()
        self.adapter.move(300, 210)
        self.assertEqual((self.host.window.x(), self.host.window.y()), (300, 210))

    def test_width_reports_the_window_width(self):
        self.assertEqual(self.adapter.width(), self.host.window.width())

    def test_height_hint_is_the_current_height(self):
        self.assertEqual(self.adapter.height_hint(), self.host.window.height())

    def test_focus_input_gives_the_search_field_focus(self):
        self.adapter.show()
        self.assertFalse(self.adapter.has_input_focus())
        self.adapter.focus_input()
        self.app.processEvents()
        self.assertTrue(self.adapter.has_input_focus())

    def test_raise_and_activate_do_not_raise(self):
        self.adapter.show()
        self.adapter.raise_window()
        self.adapter.activate()

    def test_it_satisfies_the_controller(self):
        # The whole point: PaletteWindowController must drive it unchanged.
        from window_control import PaletteWindowController

        class Native:
            def __init__(self): self.activated = []
            def foreground_handle(self): return 9999
            def activate_handle(self, h): self.activated.append(h)

        controller = PaletteWindowController(self.adapter, FakeScheduler(), Native())
        controller.is_open = True
        self.adapter.show()
        controller.begin_focus_attempts(max_attempts=3)
        self.app.processEvents()
        self.assertTrue(self.adapter.has_input_focus())
