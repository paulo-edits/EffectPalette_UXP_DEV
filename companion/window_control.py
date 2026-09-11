"""Native window behaviour for the palette, isolated behind an adapter.

Premiere does not hand focus over politely: opening the palette needs a show/raise/activate
plus a Win32 activation, retried a couple of times before the input field actually has the
caret. That retry logic is the single most fragile thing in the UI, so it lives here, gets
unit-tested against a fake, and talks to the window only through WindowAdapter -- which is
what lets a QQuickWindow replace a QWidget later without touching any of this.
"""

from __future__ import annotations

from typing import Callable, Protocol

# Delay before each retry, indexed by attempt number and clamped to the last entry.
RETRY_DELAYS_MS = (25, 75)


class WindowAdapter(Protocol):
    def show(self) -> None: ...
    def raise_window(self) -> None: ...
    def activate(self) -> None: ...
    def handle(self) -> int | None: ...
    def move(self, x: int, y: int) -> None: ...
    def width(self) -> int: ...
    def height_hint(self) -> int: ...
    def focus_input(self) -> None: ...
    def has_input_focus(self) -> bool: ...


class WidgetWindowAdapter:
    """WindowAdapter over the current QtWidgets palette."""

    def __init__(self, window, focus_widget):
        self._window = window
        self._focus_widget = focus_widget
        self._handle: int | None = None

    def show(self):
        self._window.show()

    def raise_window(self):
        self._window.raise_()

    def activate(self):
        self._window.activateWindow()

    def handle(self):
        if self._handle:
            return self._handle
        try:
            self._handle = int(self._window.winId())
        except Exception:
            return None
        return self._handle

    def move(self, x, y):
        self._window.move(x, y)

    def width(self):
        return self._window.width()

    def height_hint(self):
        return self._window.sizeHint().height()

    def focus_input(self):
        from PySide6 import QtCore

        self._focus_widget.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)

    def has_input_focus(self):
        return self._focus_widget.hasFocus()


class PaletteWindowController:
    def __init__(self, adapter: WindowAdapter, scheduler, native,
                 on_focus_acquired: Callable[[int], None] | None = None):
        self._adapter = adapter
        self._scheduler = scheduler
        self._native = native
        self._on_focus_acquired = on_focus_acquired
        self._focus_job = None
        self._previous_handle: int | None = None
        self._focus_reported = False
        self.is_open = False

    def handle(self) -> int | None:
        return self._adapter.handle()

    # --- previous-focus bookkeeping -------------------------------------------------

    @property
    def previous_handle(self) -> int | None:
        """Premiere's window, captured when the palette opened.

        The native Nest and Label paths read this to know which window to put back in
        the foreground before synthesising a keystroke, and the configured-action path
        (global hotkeys) sets it when the palette itself already owns focus.
        """
        return self._previous_handle

    @previous_handle.setter
    def previous_handle(self, handle: int | None) -> None:
        self._previous_handle = handle

    def remember_previous_focus(self) -> None:
        previous = self._native.foreground_handle()
        if previous and previous != self._adapter.handle():
            self._previous_handle = previous

    def restore_previous_focus(self) -> None:
        previous = self._previous_handle
        self._previous_handle = None
        if previous and previous != self._adapter.handle():
            self._native.activate_handle(previous)

    # --- focus acquisition ----------------------------------------------------------

    def begin_focus_attempts(self, max_attempts: int) -> None:
        self._focus_reported = False
        self._attempt_focus(0, max_attempts)

    def cancel_focus_attempts(self) -> None:
        if self._focus_job is None:
            return
        try:
            self._scheduler.after_cancel(self._focus_job)
        except Exception:
            pass
        self._focus_job = None

    def _attempt_focus(self, attempt: int, max_attempts: int) -> None:
        self._focus_job = None
        if not self.is_open:
            return
        try:
            self._adapter.show()
            self._adapter.raise_window()
            self._adapter.activate()
            self._native.activate_handle(self._adapter.handle())
            self._adapter.focus_input()
        except Exception:
            pass

        if self._adapter.has_input_focus():
            self.cancel_focus_attempts()
            if not self._focus_reported:
                self._focus_reported = True
                if self._on_focus_acquired is not None:
                    self._on_focus_acquired(attempt)
            return

        if attempt < max_attempts:
            delay = RETRY_DELAYS_MS[min(attempt, len(RETRY_DELAYS_MS) - 1)]
            self._focus_job = self._scheduler.after(
                delay, lambda: self._attempt_focus(attempt + 1, max_attempts),
            )

    # --- placement ------------------------------------------------------------------

    def anchor_to_pointer(self, pointer, available, position_chooser) -> None:
        pointer_x, pointer_y = pointer
        available_x, available_y, available_w, available_h = available
        x, y = position_chooser(
            pointer_x=pointer_x,
            pointer_y=pointer_y,
            window_width=self._adapter.width(),
            window_height=self._adapter.height_hint(),
            screen_width=available_w,
            screen_height=available_h,
        )
        self._adapter.move(available_x + x, available_y + y)


class QuickWindowAdapter:
    """WindowAdapter over a QQuickWindow.

    QQuickWindow is a QWindow, not a QWidget: it activates with requestActivate() rather
    than activateWindow(), positions with setPosition() rather than move(), and has no
    sizeHint(). Focus lives in QML, so the root object must declare a focusSearch()
    function and a searchHasFocus property.

    The window is larger than the palette the user sees: a transparent shadowMargin on
    every side holds the QML-drawn shadow. Geometry here is the visible shell's, so the
    controller's placement is unchanged by the margin.
    """

    def __init__(self, window):
        self._window = window
        self._handle: int | None = None

    def show(self):
        self._window.show()

    def raise_window(self):
        self._window.raise_()

    def activate(self):
        self._window.requestActivate()

    def handle(self):
        if self._handle:
            return self._handle
        try:
            self._handle = int(self._window.winId())
        except Exception:
            return None
        return self._handle

    def _margin(self) -> int:
        return int(self._window.property("shadowMargin") or 0)

    def move(self, x, y):
        margin = self._margin()
        self._window.setPosition(int(x) - margin, int(y) - margin)

    def width(self):
        return self._window.width() - 2 * self._margin()

    def height_hint(self):
        # QML sizes itself from its content, so the current height is the hint.
        return self._window.height() - 2 * self._margin()

    def focus_input(self):
        self._window.focusSearch()

    def has_input_focus(self):
        return bool(self._window.property("searchHasFocus"))

    def shell_rect(self):
        """The visible palette in screen coordinates: x, y, width, height."""
        margin = self._margin()
        return (self._window.x() + margin, self._window.y() + margin,
                self.width(), self.height_hint())


class ShadowMarginPassThrough:
    """Lets clicks in the palette's transparent shadow margin reach the window behind.

    Windows hit-tests the whole native window, transparent pixels included, and its
    click-through switch (WS_EX_TRANSPARENT) covers the whole window too. So while the
    palette is open this polls the pointer: outside the visible shell the window is
    click-through, inside it is solid. Only mouse hit-testing changes; keyboard focus
    stays with the palette.
    """

    POLL_MS = 30

    def __init__(self, *, shell_rect, cursor_pos, handle, set_click_through, scheduler,
                 interval_ms: int = POLL_MS):
        self._shell_rect = shell_rect
        self._cursor_pos = cursor_pos
        self._handle = handle
        self._set_click_through = set_click_through
        self._scheduler = scheduler
        self._interval_ms = interval_ms
        self._job = None
        self._click_through = False
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._tick()

    def stop(self) -> None:
        self.running = False
        if self._job is not None:
            self._scheduler.after_cancel(self._job)
            self._job = None
        if self._click_through:
            self._apply(False)

    def _apply(self, enabled: bool) -> None:
        handle = self._handle()
        if not handle:
            return
        self._set_click_through(handle, enabled)
        self._click_through = enabled

    def _tick(self) -> None:
        self._job = None
        if not self.running:
            return
        x, y = self._cursor_pos()
        left, top, width, height = self._shell_rect()
        inside = left <= x < left + width and top <= y < top + height
        if (not inside) != self._click_through:
            self._apply(not inside)
        self._job = self._scheduler.after(self._interval_ms, self._tick)
