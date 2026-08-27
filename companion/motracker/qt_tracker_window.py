"""Motion Tracker window - interactive point-tracking UI hosted natively in Qt instead of a UXP
HTML/canvas panel. See TECHNICAL_PLAN.md's Motion Tracker slice for why: UXP's <canvas> has no
drawImage/transform support, so the original UXP port simulated a preview via CSS-positioned <img>
elements + SVG innerHTML overlays, forcing a DOM reflow/reparse on every frame update.
QGraphicsPixmapItem.setPixmap() here is a single real blit per frame instead - that swap is the
actual fix for the reported slowness.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import tracker_engine
from .extraction import FrameExtractor


def _make_transport_icon(kind: str, color: str, size: int = 16) -> QtGui.QIcon:
    """Draws the transport-control glyphs (play/pause/skip) ourselves via QPainter instead of
    using Qt's QStyle standard icons - those render as flat black glyphs regardless of the app's
    own stylesheet (a real host test found them low-contrast and slightly off-center against this
    window's dark theme), so this gives full control over color and centering with plain shapes,
    no external icon asset needed."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(color))
    m = size * 0.22  # margin
    if kind == "play":
        painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(m, m), QtCore.QPointF(size - m, size / 2), QtCore.QPointF(m, size - m)]))
    elif kind == "pause":
        bar_w = size * 0.22
        painter.drawRect(QtCore.QRectF(m, m, bar_w, size - 2 * m))
        painter.drawRect(QtCore.QRectF(size - m - bar_w, m, bar_w, size - 2 * m))
    elif kind in ("skip_back", "skip_fwd"):
        bar_w = size * 0.14
        tri = [QtCore.QPointF(size - m, m), QtCore.QPointF(m + bar_w * 1.6, size / 2), QtCore.QPointF(size - m, size - m)]
        bar_x = m
        if kind == "skip_fwd":
            tri = [QtCore.QPointF(m, m), QtCore.QPointF(size - m - bar_w * 1.6, size / 2), QtCore.QPointF(m, size - m)]
            bar_x = size - m - bar_w
        painter.drawPolygon(QtGui.QPolygonF(tri))
        painter.drawRect(QtCore.QRectF(bar_x, m, bar_w, size - 2 * m))
    painter.end()
    return QtGui.QIcon(pixmap)


class PointSignalRelay(QtCore.QObject):
    moved = QtCore.Signal(float, float)
    released = QtCore.Signal()


class TrackPointItem(QtWidgets.QGraphicsEllipseItem):
    """Draggable tracking-point handle. QGraphicsItem isn't itself a QObject and can't have
    signals, so moves are reported through a companion QObject (PointSignalRelay)."""

    RADIUS = 2.5
    DEFAULT_HIT_HALF = 11.0

    def __init__(self, signal_relay: PointSignalRelay):
        super().__init__(-self.RADIUS, -self.RADIUS, self.RADIUS * 2, self.RADIUS * 2)
        self._relay = signal_relay
        self._bounds = QtCore.QRectF()
        # The draggable area is the WHOLE feature-radius square (see set_hit_half, called from
        # FrameView.update_marker whenever the feature box's own size changes) - not just this
        # tiny visual dot. A real host test found dragging only the dot itself needed a
        # pixel-perfect click; matching the much bigger, clearly-visible orange square the user
        # already looks at makes it obvious and easy to grab anywhere inside it.
        self._hit_half = self.DEFAULT_HIT_HALF
        # Small, high-contrast orange dot, no stroke - a plain light fill (the previous green) all
        # but disappeared against a bright/white background frame, per a real host test.
        self.setBrush(QtGui.QBrush(QtGui.QColor("#FF8A00")))
        self.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(10)

    def set_hit_half(self, half: float):
        """Resizes the invisible draggable area to a square of this half-width, independent of
        the tiny visual dot (paint() still draws only the small circle from rect())."""
        self.prepareGeometryChange()
        self._hit_half = max(self.DEFAULT_HIT_HALF, half)

    def boundingRect(self) -> QtCore.QRectF:
        r = self._hit_half
        return QtCore.QRectF(-r, -r, r * 2, r * 2)

    def shape(self) -> QtGui.QPainterPath:
        # A square, not a circle, so the whole visible orange feature box the user is looking at
        # is grabbable, corners included - not just an inscribed circle within it.
        path = QtGui.QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def set_bounds(self, rect: QtCore.QRectF):
        self._bounds = rect

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._bounds.isValid():
            x = min(max(value.x(), self._bounds.left()), self._bounds.right())
            y = min(max(value.y(), self._bounds.top()), self._bounds.bottom())
            return QtCore.QPointF(x, y)
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._relay.moved.emit(self.pos().x(), self.pos().y())
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._relay.released.emit()


class FrameView(QtWidgets.QGraphicsView):
    """Displays one frame at a time via a single QGraphicsPixmapItem, added at native resolution -
    a click's view coordinates map straight to source-frame pixel coordinates via mapToScene(),
    with no manual scale/letterbox math, since fitInView() only ever changes the view's own
    transform, never the pixmap item's geometry."""

    pointPlaced = QtCore.Signal(float, float)
    pointMoved = QtCore.Signal(float, float)
    # Fired once when a drag-to-correct ends (mouse released), separate from pointMoved which
    # fires on every intermediate position during the drag itself - see its own docstring in
    # MotionTrackerWindow._on_point_dragged for why the split matters (perf).
    pointDragFinished = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Qt's own anchor-under-mouse does the zoom-to-cursor math CEP's wheel handler had to do
        # by hand (recentering pan around the cursor) - no manual pan bookkeeping needed here.
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)
        # True once the user has manually zoomed (wheel) - suppresses the auto fit-to-view on
        # resize/showEvent so a resize doesn't silently undo a manual zoom; cleared by fit_to_view's
        # own explicit "Fit" button (see MotionTrackerWindow), not double-click - a double-click on
        # this canvas already means "place the point here" (see mousePressEvent), so binding
        # dblclick to fit-to-view like CEP does would silently move the point too.
        self._manual_zoom = False
        self._panning = False
        self._pan_start = QtCore.QPoint()

        self._pixmap_item = QtWidgets.QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)

        self._point_relay = PointSignalRelay(self)
        self._point_relay.moved.connect(self.pointMoved)
        self._point_relay.released.connect(self.pointDragFinished)
        self._point_item: TrackPointItem | None = None

        self._path_items: list[QtWidgets.QGraphicsPathItem] = []
        self._frame_w = 0
        self._frame_h = 0
        self._follow_overlay_item: QtWidgets.QGraphicsItemGroup | None = None

        # Search/feature-radius halo + crosshair around the active point, matching pFX-Tracker's
        # own marker (drawActivePoint in main.js) - purely visual, non-interactive (dragging still
        # goes through _point_item alone), repositioned in update_marker() on every frame change.
        # Each shape has a solid black "shadow" twin directly beneath it (same geometry, z-1) -
        # a plain white/amber line all but disappeared against a bright/white background frame,
        # per a real host test. The search box's shadow is solid (not dashed) so the white dashes
        # on top read as alternating white/black "marching ants", visible on any background.
        self._search_box_shadow = QtWidgets.QGraphicsRectItem()
        self._search_box_shadow.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 200), 2.2))
        self._search_box_shadow.setZValue(8)
        self._search_box_shadow.setVisible(False)
        self._scene.addItem(self._search_box_shadow)

        self._search_box = QtWidgets.QGraphicsRectItem()
        self._search_box.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 210), 1.3, QtCore.Qt.PenStyle.DashLine))
        self._search_box.setZValue(9)
        self._search_box.setVisible(False)
        self._scene.addItem(self._search_box)

        self._feature_box_shadow = QtWidgets.QGraphicsRectItem()
        self._feature_box_shadow.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 200), 2.8))
        self._feature_box_shadow.setZValue(8)
        self._feature_box_shadow.setVisible(False)
        self._scene.addItem(self._feature_box_shadow)

        self._feature_box = QtWidgets.QGraphicsRectItem()
        self._feature_box.setPen(QtGui.QPen(QtGui.QColor("#FFC332"), 1.5))
        self._feature_box.setZValue(9)
        self._feature_box.setVisible(False)
        self._scene.addItem(self._feature_box)

        self._crosshair_shadow = [QtWidgets.QGraphicsLineItem() for _ in range(4)]
        for line in self._crosshair_shadow:
            line.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 200), 2.8))
            line.setZValue(8)
            line.setVisible(False)
            self._scene.addItem(line)

        self._crosshair = [QtWidgets.QGraphicsLineItem() for _ in range(4)]
        for line in self._crosshair:
            line.setPen(QtGui.QPen(QtGui.QColor("#FFC332"), 1.5))
            line.setZValue(9)
            line.setVisible(False)
            self._scene.addItem(line)

    def set_frame(self, pixmap: QtGui.QPixmap):
        self._pixmap_item.setPixmap(pixmap)
        self._pixmap_item.setPos(0, 0)
        self._frame_w, self._frame_h = pixmap.width(), pixmap.height()
        self._scene.setSceneRect(0, 0, self._frame_w, self._frame_h)
        if self._point_item is not None:
            self._point_item.set_bounds(QtCore.QRectF(0, 0, self._frame_w, self._frame_h))

    def set_stabilize_offset(self, dx: float, dy: float):
        """"Simulate result" preview for Stabilize - shifts the WHOLE displayed frame by the
        negative of the tracked point's own displacement from the first tracked frame, visually
        canceling the camera shake locally. Pure preview: no Premiere writes at all, and
        fit_to_view() fits against the scene's fixed rect (see its own docstring) so this
        translation is actually visible instead of being re-centered away on every frame."""
        self._pixmap_item.setPos(dx, dy)

    FOLLOW_OVERLAY_RADIUS = 22.0

    def set_follow_overlay_visible(self, visible: bool):
        """"Simulate result" preview for Apply Track (follow) - a simple app-drawn target reticle
        (a translucent accent-colored ring + crosshair, NOT the followed object's real image, per
        the user's own round-3 feedback: it's just a stand-in shape, and drawing it ourselves also
        sidesteps the "stills only, not video" limitation the real-image version had) shown at the
        tracked point each frame (see position_follow_overlay), translation only - a rough
        approximation matching what Position-only movement actually does in Premiere."""
        if self._follow_overlay_item is None and visible:
            group = QtWidgets.QGraphicsItemGroup()
            r = self.FOLLOW_OVERLAY_RADIUS
            ring = QtWidgets.QGraphicsEllipseItem(-r, -r, r * 2, r * 2)
            ring.setBrush(QtGui.QBrush(QtGui.QColor(114, 120, 240, 90)))
            ring.setPen(QtGui.QPen(QtGui.QColor("#7278F0"), 2.0))
            group.addToGroup(ring)
            arm = r * 0.6
            for x1, y1, x2, y2 in ((-arm, 0.0, arm, 0.0), (0.0, -arm, 0.0, arm)):
                line = QtWidgets.QGraphicsLineItem(x1, y1, x2, y2)
                line.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 1.5))
                group.addToGroup(line)
            group.setZValue(6)
            self._scene.addItem(group)
            self._follow_overlay_item = group
        if self._follow_overlay_item is not None:
            self._follow_overlay_item.setVisible(visible)

    def position_follow_overlay(self, x: float, y: float):
        if self._follow_overlay_item is not None:
            self._follow_overlay_item.setPos(x, y)

    def fit_to_view(self):
        # Guarded on _manual_zoom so callers that fire on every frame change (e.g. _show_frame,
        # to keep a freshly-loaded pixmap properly fit) don't silently undo a manual wheel-zoom
        # while scrubbing. reset_zoom() is the one legitimate way back to auto-fit. Fits against
        # the scene's own fixed (0,0,w,h) rect rather than the pixmap item's bounding rect - the
        # stabilize-preview mode moves the pixmap item itself (see set_stabilize_offset), and
        # fitting to a moving item would just re-center on it every frame, canceling out the very
        # translation the preview exists to show.
        if self._frame_w and self._frame_h and not self._manual_zoom:
            self.fitInView(QtCore.QRectF(0, 0, self._frame_w, self._frame_h), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def reset_zoom(self):
        self._manual_zoom = False
        if self._frame_w and self._frame_h:
            self.fitInView(QtCore.QRectF(0, 0, self._frame_w, self._frame_h), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_to_view()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_view()

    def wheelEvent(self, event):
        if not (self._frame_w and self._frame_h):
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 0.85
        new_scale = self.transform().m11() * factor
        if 0.05 <= new_scale <= 40:
            self._manual_zoom = True
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            scene_pt = self.mapToScene(event.pos())
            item_at = self._scene.itemAt(scene_pt, self.transform())
            if item_at is not self._point_item:
                self.place_point(scene_pt.x(), scene_pt.y())
                self.pointPlaced.emit(scene_pt.x(), scene_pt.y())
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def place_point(self, x: float, y: float):
        x = min(max(x, 0.0), max(0.0, self._frame_w - 1))
        y = min(max(y, 0.0), max(0.0, self._frame_h - 1))
        if self._point_item is None:
            self._point_item = TrackPointItem(self._point_relay)
            self._point_item.set_bounds(QtCore.QRectF(0, 0, self._frame_w, self._frame_h))
            self._scene.addItem(self._point_item)
        self._point_item.setPos(x, y)

    def current_point(self) -> tuple[float, float] | None:
        if self._point_item is None:
            return None
        pos = self._point_item.pos()
        return pos.x(), pos.y()

    def clear_point(self):
        """Removes the point + its whole marker halo - used on Reload so a previous clip's
        leftover point/track doesn't linger visually on the newly-loaded one."""
        if self._point_item is not None:
            self._scene.removeItem(self._point_item)
            self._point_item = None
        for item in (self._search_box, self._search_box_shadow, self._feature_box, self._feature_box_shadow, *self._crosshair, *self._crosshair_shadow):
            item.setVisible(False)

    def set_marker_visible(self, visible: bool):
        """Hides/shows the whole marker (point + halo) without discarding it - used while a
        "simulate result" preview is active, so the marker doesn't clutter the preview."""
        if self._point_item is not None:
            self._point_item.setVisible(visible)
        if not visible:
            for item in (self._search_box, self._search_box_shadow, self._feature_box, self._feature_box_shadow, *self._crosshair, *self._crosshair_shadow):
                item.setVisible(False)

    def update_marker(self, search_r: float, feature_r: float, dimmed: bool, edited: bool):
        """Reposition the search/feature halo + crosshair around the active point and dim them on
        a lost-confidence frame, or tint the feature box brighter on a manually-edited frame -
        matches pFX-Tracker's own marker exactly (see drawActivePoint in main.js)."""
        if self._point_item is None:
            return
        self._point_item.set_hit_half(feature_r)
        pos = self._point_item.pos()
        x, y = pos.x(), pos.y()
        opacity = 0.35 if dimmed else 1.0

        search_rect = QtCore.QRectF(x - search_r, y - search_r, search_r * 2, search_r * 2)
        self._search_box_shadow.setRect(search_rect)
        self._search_box_shadow.setOpacity(opacity)
        self._search_box_shadow.setVisible(True)
        self._search_box.setRect(search_rect)
        self._search_box.setOpacity(opacity)
        self._search_box.setVisible(True)

        feature_color = QtGui.QColor("#FFDC3C") if edited else QtGui.QColor("#FFC332")
        feature_rect = QtCore.QRectF(x - feature_r, y - feature_r, feature_r * 2, feature_r * 2)
        self._feature_box_shadow.setRect(feature_rect)
        self._feature_box_shadow.setOpacity(opacity)
        self._feature_box_shadow.setVisible(True)
        self._feature_box.setPen(QtGui.QPen(feature_color, 1.5))
        self._feature_box.setRect(feature_rect)
        self._feature_box.setOpacity(opacity)
        self._feature_box.setVisible(True)

        arm, gap = 8.0, 5.0
        offsets = [
            (-arm - gap, 0.0, -gap, 0.0), (gap, 0.0, arm + gap, 0.0),
            (0.0, -arm - gap, 0.0, -gap), (0.0, gap, 0.0, arm + gap),
        ]
        for shadow, line, (x1, y1, x2, y2) in zip(self._crosshair_shadow, self._crosshair, offsets):
            shadow.setLine(x + x1, y + y1, x + x2, y + y2)
            shadow.setOpacity(opacity)
            shadow.setVisible(True)
            line.setLine(x + x1, y + y1, x + x2, y + y2)
            line.setPen(QtGui.QPen(feature_color, 1.5))
            line.setOpacity(opacity)
            line.setVisible(True)

    def set_path(self, tracks: list[dict], conf_thresh: float):
        for item in self._path_items:
            self._scene.removeItem(item)
        self._path_items = []
        if len(tracks) < 2:
            return

        def band(conf: float) -> QtGui.QColor:
            if conf >= 0.65:
                return QtGui.QColor("#4FD67A")
            if conf >= conf_thresh:
                return QtGui.QColor("#F5C542")
            return QtGui.QColor("#E0524F")

        current_color = None
        path = None
        for point in tracks:
            color = band(point["conf"])
            if color != current_color:
                if path is not None and path.elementCount() > 1:
                    self._add_path(path, current_color)
                path = QtGui.QPainterPath()
                path.moveTo(point["x"], point["y"])
                current_color = color
            else:
                path.lineTo(point["x"], point["y"])
        if path is not None and path.elementCount() > 1:
            self._add_path(path, current_color)

    def _add_path(self, path: QtGui.QPainterPath, color: QtGui.QColor):
        item = QtWidgets.QGraphicsPathItem(path)
        item.setPen(QtGui.QPen(color, 2.0))
        item.setZValue(5)
        self._scene.addItem(item)
        self._path_items.append(item)


class ConfidenceScrubber(QtWidgets.QWidget):
    """Custom-painted scrubber replacing the plain QSlider - a per-frame confidence waveform (bar
    height/color = tracked confidence), edited-frame tick marks, and a white playhead cursor,
    ported from pFX-Tracker's own renderScrubber/drawScrubCursor (main.js). Colors reuse the same
    palette FrameView.set_path/update_marker already use (#4FD67A/#F5C542/#E0524F), rather than
    CEP's own slightly different hex, so the port stays internally consistent."""

    seeked = QtCore.Signal(int)
    rangeChanged = QtCore.Signal(int, int)  # in_frame, out_frame (-1, -1 = cleared)

    LOST_COLOR = QtGui.QColor(90, 90, 90, 140)
    GOOD_COLOR = QtGui.QColor("#4FD67A")
    MID_COLOR = QtGui.QColor("#F5C542")
    LOW_COLOR = QtGui.QColor("#E0524F")
    EDITED_COLOR = QtGui.QColor("#FFDC3C")
    RANGE_COLOR = QtGui.QColor("#7278F0")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(30)
        self.setMaximumHeight(30)
        self._frame_count = 0
        self._current_frame = 0
        self._tracks_by_frame: dict[int, dict] = {}
        self._edited_frames: set[int] = set()
        self._in_frame = -1
        self._out_frame = -1
        # Set while a Shift+drag is actively defining a new range, so a plain drag-to-seek doesn't
        # also start dragging one - matches CEP's own shift-drag-for-range convention exactly.
        self._range_anchor = -1

    def set_range(self, frame_count: int):
        self._frame_count = frame_count
        self.update()

    def set_current_frame(self, index: int):
        self._current_frame = index
        self.update()

    def set_tracks(self, tracks_by_frame: dict[int, dict], edited_frames: set[int]):
        self._tracks_by_frame = tracks_by_frame
        self._edited_frames = edited_frames
        self.update()

    def set_in_out(self, in_frame: int, out_frame: int):
        self._in_frame = in_frame
        self._out_frame = out_frame
        self.update()

    def _frame_at_x(self, x: float) -> int:
        if self._frame_count <= 1:
            return 0
        pct = max(0.0, min(1.0, x / max(1, self.width())))
        return round(pct * (self._frame_count - 1))

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton or not self._frame_count:
            return
        frame = self._frame_at_x(event.position().x())
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
            self._range_anchor = frame
            self._in_frame = self._out_frame = frame
            self.rangeChanged.emit(self._in_frame, self._out_frame)
            self.update()
            return
        self._range_anchor = -1
        self.seeked.emit(frame)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & QtCore.Qt.MouseButton.LeftButton) or not self._frame_count:
            return
        frame = self._frame_at_x(event.position().x())
        if self._range_anchor >= 0:
            self._in_frame = min(self._range_anchor, frame)
            self._out_frame = max(self._range_anchor, frame)
            self.rangeChanged.emit(self._in_frame, self._out_frame)
            self.update()
            return
        self.seeked.emit(frame)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QtGui.QColor("#0c0b10"))
        n = self._frame_count
        if n > 0:
            for i in range(n):
                track = self._tracks_by_frame.get(i)
                if track is None:
                    continue
                x1 = int(i / n * w)
                x2 = max(x1 + 1, int((i + 1) / n * w))
                conf = track.get("conf", 0.0)
                if conf <= 0:
                    bar_h, color = 2, self.LOST_COLOR
                else:
                    bar_h = max(3, round(conf * (h - 2)))
                    color = self.GOOD_COLOR if conf > 0.6 else self.MID_COLOR if conf > 0.3 else self.LOW_COLOR
                painter.fillRect(x1, h - bar_h, x2 - x1, bar_h, color)
            if self._edited_frames and n > 1:
                for frame in self._edited_frames:
                    ex = round(frame / (n - 1) * w)
                    painter.fillRect(ex - 1, 0, 2, 5, self.EDITED_COLOR)
            if self._in_frame >= 0 and self._out_frame >= 0 and n > 1:
                ix = round(self._in_frame / (n - 1) * w)
                ox = round(self._out_frame / (n - 1) * w)
                tint = QtGui.QColor(self.RANGE_COLOR)
                tint.setAlpha(45)
                painter.fillRect(min(ix, ox), 0, max(1, abs(ox - ix)), h, tint)
                painter.fillRect(ix - 1, 0, 2, h, self.RANGE_COLOR)
                painter.fillRect(ox - 1, 0, 2, h, self.RANGE_COLOR)
            cx = round(self._current_frame / max(1, n - 1) * w) if n > 1 else 0
            painter.fillRect(cx - 1, 0, 2, h, QtGui.QColor(255, 255, 255, 235))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor("#ffffff"))
            painter.drawEllipse(QtCore.QPointF(cx, 5), 4, 4)
        painter.end()


class TrackWorker(QtCore.QObject):
    """Runs tracker_engine.track() in a background QThread - CPU-bound work that would otherwise
    freeze the UI. Qt signal emission across threads is already thread-safe (auto QueuedConnection
    when emitter/receiver live on different threads), so no manual thread-marshaling is needed here,
    unlike the original asyncio daemon's call_soon_threadsafe workaround for the same problem."""

    progress = QtCore.Signal(dict)
    finished = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    @QtCore.Slot(dict)
    def run(self, job: dict):
        self._cancel_event.clear()
        try:
            if job.get("bidirectional"):
                tracks = self._run_bidirectional(job)
                result = {"tracks": tracks, "engine": "csrt-hybrid"}
            else:
                result = tracker_engine.track(job, on_progress=self.progress.emit, should_cancel=self._cancel_event.is_set)
            self.finished.emit(result)
        except tracker_engine.Cancelled:
            self.failed.emit("Cancelled.")
        except Exception as error:  # noqa: BLE001 - must always answer
            self.failed.emit(f"{type(error).__name__}: {error}")

    def _run_bidirectional(self, job: dict) -> list[dict]:
        # Forward then backward, sequentially in this same worker thread - not two concurrent
        # threads, since CPU-bound cv2/numpy work doesn't get faster running "concurrently" past
        # the GIL, and running sequentially avoids two workers contending for the same cores for
        # no benefit. Progress blending (pct weighted by each pass's own frame count) matches the
        # original daemon's run_track exactly, so Track still shows one smooth 0..1 progress bar.
        start_frame = int(job.get("startFrame", 0))
        frame_count = int(job.get("frameCount", 0))
        fwd_frames = max(1, frame_count - start_frame)
        bwd_frames = start_frame
        total_frames = fwd_frames + bwd_frames

        def fwd_progress(p: dict):
            blended = dict(p)
            blended["pct"] = round((p["pct"] * fwd_frames) / total_frames, 4)
            self.progress.emit(blended)

        def bwd_progress(p: dict):
            blended = dict(p)
            blended["pct"] = round((fwd_frames + p["pct"] * bwd_frames) / total_frames, 4)
            self.progress.emit(blended)

        fwd_job = {**job, "direction": "forward"}
        fwd_result = tracker_engine.track(fwd_job, on_progress=fwd_progress, should_cancel=self._cancel_event.is_set)
        merged: dict[int, dict] = {t["frame"]: t for t in fwd_result["tracks"]}
        if start_frame > 0:
            bwd_job = {**job, "direction": "backward"}
            bwd_result = tracker_engine.track(bwd_job, on_progress=bwd_progress, should_cancel=self._cancel_event.is_set)
            for t in bwd_result["tracks"]:
                merged.setdefault(t["frame"], t)
        return [merged[f] for f in sorted(merged.keys())]


class MotionTrackerWindow(QtWidgets.QDialog):
    """Non-modal top-level window - lifetime independent of the search palette (only closing this
    window itself destroys it, hiding/reopening the palette never touches it), same pattern as
    QtSettingsCenter/DebugWindow in companion/app.py."""

    def __init__(self, adapter=None, parent=None, ui_font_family: str = "Segoe UI", accent: str = "#7278F0"):
        super().__init__(parent)
        self.setWindowTitle("FX.palette — Motion Tracker")
        self.resize(980, 860)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self._accent = accent
        self._apply_style(ui_font_family, accent)

        self._adapter = adapter
        self._clip_info: dict | None = None
        self._extractor: FrameExtractor | None = None
        self._auto_loaded = False

        self._frames_dir: Path | None = None
        self._frame_count = 0
        self._current_frame = 0
        self._tracks: list[dict] = []
        self._tracks_by_frame: dict[int, dict] = {}
        # The pristine, un-smoothed track - de-shake always recomputes from this (never from an
        # already-smoothed result), so re-tuning the strength slider never compounds. A manual
        # point correction (_on_point_dragged) is written back here too, so it becomes the new
        # ground truth for future re-smoothing instead of being overwritten by it.
        self._tracks_raw: list[dict] = []
        # Frames the user manually dragged the point on - dims/tints the marker like CEP's own
        # (brighter yellow feature box on an edited frame, see FrameView.update_marker).
        self._edited_frames: set[int] = set()
        # Set while _show_frame is moving the point to a frame's already-tracked position, so
        # that programmatic move is not mistaken for a manual drag-to-correct in _on_point_dragged.
        self._syncing_point = False

        # In/Out range - a refinement tool for AFTER a track exists (delete or re-track just that
        # span), not a gate on the initial Track call - mirrors CEP exactly (its own main Track
        # button ignores In/Out; only "Delete"/"Re-track" in the range-actions row use it).
        self._in_frame = -1
        self._out_frame = -1

        # The frame the user actually clicked the point on for the current track - applyTrack's
        # delta-reference frame (see index.js's seedFrame handling for why this matters).
        self._track_seed_frame: int | None = None

        self._track_thread: QtCore.QThread | None = None
        self._track_worker: TrackWorker | None = None
        self._retrack_thread: QtCore.QThread | None = None
        self._retrack_worker: TrackWorker | None = None

        self._apply_timestamp: float | None = None
        self._apply_poll_timer = QtCore.QTimer(self)
        self._apply_poll_timer.timeout.connect(self._poll_apply_track)

        self._playing = False
        self._play_timer = QtCore.QTimer(self)
        self._play_timer.timeout.connect(self._on_play_tick)
        self._last_live_nav_time = 0.0

        self._build()

    def _apply_style(self, ui_font_family: str, accent: str):
        # Same dark palette/control conventions as the rest of FX.palette's Qt UI (see
        # QtSettingsCenter's stylesheet in companion/app.py) - kept in sync via constructor
        # params from show_motion_tracker() rather than importing app.py directly (app.py already
        # imports this module, so the reverse import would be circular).
        self.setStyleSheet(f"""
            QDialog, QWidget {{ background: #17171b; color: #f1f1f4; font-family: "{ui_font_family}"; }}
            QLabel {{ background: transparent; }}
            QLineEdit, QSpinBox, QComboBox {{
                background: #24242b; border: 1px solid #3b3b45; border-radius: 6px; padding: 5px 7px;
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}
            QPushButton {{
                background: #2c2c34; border: 1px solid #44444f; border-radius: 6px; padding: 7px 12px;
            }}
            QPushButton:hover {{ background: #393943; }}
            QPushButton:disabled {{ color: #6c6c76; }}
            /* NOTE: ":checkable" is not a real Qt Style Sheet pseudo-state (silently never
               matches if you write "QPushButton:checkable:checked") - ":checked" alone already
               only applies to checkable buttons that are actually checked, which is exactly what
               every toggle in this window (direction, motion-blur angle, preview mode buttons)
               needs to make the currently-selected option visually obvious. */
            QPushButton:checked {{ background: {accent}; border-color: {accent}; color: #0d0d10; font-weight: 600; }}
            QPushButton:checked:hover {{ background: {accent}; }}
            QPushButton#primary {{ background: {accent}; border-color: {accent}; color: #0d0d10; font-weight: 600; }}
            QPushButton#primary:hover {{ background: {accent}; }}
            QCheckBox {{ spacing: 6px; }}
            QSlider::groove:horizontal {{ height: 4px; background: #33333c; border-radius: 2px; }}
            QSlider::handle:horizontal {{
                width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {accent};
            }}
            QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
            QProgressBar {{ background: #24242b; border: 1px solid #3b3b45; border-radius: 6px; text-align: center; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
            /* Section grouping (Reprodução / Rastreamento / Prévia e aplicação) - gives the window
               real visual hierarchy instead of one long undifferentiated stack of rows. */
            QGroupBox {{
                border: 1px solid #2c2c34; border-radius: 8px; margin-top: 12px;
                padding: 14px 10px 10px 10px; font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; subcontrol-position: top left; left: 10px; top: -2px;
                padding: 0 5px; color: {accent}; background: #17171b;
            }}
        """)

    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Status + a small reload affordance, not a primary "Load" button - the window auto-loads
        # whatever clip is selected in Premiere the moment it opens (see showEvent), so a manual
        # Load step is only needed again if the user changes the timeline selection afterward
        # (Premiere has no push notification for that into this window).
        status_row = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("Carregando o clipe selecionado...")
        self.load_btn = QtWidgets.QPushButton("Recarregar")
        self.load_btn.setToolTip("Selecionou outro clipe no Premiere? Clique aqui pra recarregar.")
        self.load_btn.clicked.connect(self._on_load_clicked)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.load_btn)
        root.addLayout(status_row)

        self.view = FrameView(self)
        self.view.pointPlaced.connect(self._on_point_placed)
        self.view.pointMoved.connect(self._on_point_dragged)
        self.view.pointDragFinished.connect(self._on_point_drag_finished)
        root.addWidget(self.view, 1)

        # "Fit" resets a manual wheel-zoom back to fit-to-window - not bound to double-click like
        # CEP does on its own canvas, since a double-click here already means "place the point"
        # (see FrameView.mousePressEvent) and overloading it would silently move the point too.
        zoom_row = QtWidgets.QHBoxLayout()
        zoom_row.addStretch(1)
        fit_btn = QtWidgets.QPushButton("⤢ Ajustar zoom")
        fit_btn.setToolTip("Roda do mouse pra zoom, botão do meio pra arrastar. Esse botão volta a caber na tela.")
        fit_btn.clicked.connect(self.view.reset_zoom)
        zoom_row.addWidget(fit_btn)
        root.addLayout(zoom_row)

        playback_group = QtWidgets.QGroupBox("Reprodução")
        playback_layout = QtWidgets.QVBoxLayout(playback_group)

        # Icons drawn ourselves (_make_transport_icon) in the theme's own light text color -
        # Qt's built-in QStyle standard icons render as flat black glyphs regardless of this
        # window's dark stylesheet, which a real host test found low-contrast and off-center.
        icon_color = "#f1f1f4"
        transport_row = QtWidgets.QHBoxLayout()
        transport_row.setSpacing(6)
        self.prev_btn = QtWidgets.QPushButton()
        self.prev_btn.setIcon(_make_transport_icon("skip_back", icon_color))
        self.prev_btn.setIconSize(QtCore.QSize(16, 16))
        self.prev_btn.setFixedSize(36, 30)
        self.prev_btn.setToolTip("Frame anterior (←)")
        self.prev_btn.clicked.connect(lambda: self._step_frame(-1))
        self.play_btn = QtWidgets.QPushButton()
        self._play_icon = _make_transport_icon("play", icon_color)
        self._pause_icon = _make_transport_icon("pause", icon_color)
        self.play_btn.setIcon(self._play_icon)
        self.play_btn.setIconSize(QtCore.QSize(16, 16))
        self.play_btn.setFixedSize(36, 30)
        self.play_btn.setToolTip("Play / Pause (Espaço)")
        self.play_btn.clicked.connect(self._toggle_playback)
        self.next_btn = QtWidgets.QPushButton()
        self.next_btn.setIcon(_make_transport_icon("skip_fwd", icon_color))
        self.next_btn.setIconSize(QtCore.QSize(16, 16))
        self.next_btn.setFixedSize(36, 30)
        self.next_btn.setToolTip("Próximo frame (→)")
        self.next_btn.clicked.connect(lambda: self._step_frame(1))
        transport_row.addWidget(self.prev_btn)
        transport_row.addWidget(self.play_btn)
        transport_row.addWidget(self.next_btn)
        transport_row.addSpacing(14)
        # Sized to fit "OUT" with the button's own padding, not a tight fixed width - the previous
        # 44px cut the text off per a real host test.
        self.in_btn = QtWidgets.QPushButton("IN")
        self.in_btn.setMinimumWidth(54)
        self.in_btn.setToolTip("Marcar ponto de entrada aqui (I) - ou Shift+arraste na timeline")
        self.in_btn.clicked.connect(self._on_set_in_point)
        self.out_btn = QtWidgets.QPushButton("OUT")
        self.out_btn.setMinimumWidth(54)
        self.out_btn.setToolTip("Marcar ponto de saída aqui (O) - ou Shift+arraste na timeline")
        self.out_btn.clicked.connect(self._on_set_out_point)
        transport_row.addWidget(self.in_btn)
        transport_row.addWidget(self.out_btn)
        transport_row.addStretch(1)
        playback_layout.addLayout(transport_row)

        self.scrubber = ConfidenceScrubber(self)
        self.scrubber.seeked.connect(self._on_scrub)
        self.scrubber.rangeChanged.connect(self._on_range_changed)
        playback_layout.addWidget(self.scrubber)

        # Range refinement - a correction tool for AFTER a track exists (delete or re-track just
        # the marked span), matching CEP's own range-actions row exactly; hidden until a range is
        # actually set (see _update_range_actions).
        range_row = QtWidgets.QHBoxLayout()
        self.range_label = QtWidgets.QLabel("")
        self.delete_range_btn = QtWidgets.QPushButton("Apagar intervalo")
        self.delete_range_btn.clicked.connect(self._on_delete_range)
        self.retrack_range_btn = QtWidgets.QPushButton("Retrackear intervalo")
        self.retrack_range_btn.clicked.connect(self._on_retrack_range)
        self.clear_range_btn = QtWidgets.QPushButton("✕")
        self.clear_range_btn.setFixedWidth(28)
        self.clear_range_btn.setToolTip("Limpar seleção (Esc)")
        self.clear_range_btn.clicked.connect(self._clear_range)
        range_row.addWidget(self.range_label)
        range_row.addStretch(1)
        range_row.addWidget(self.delete_range_btn)
        range_row.addWidget(self.retrack_range_btn)
        range_row.addWidget(self.clear_range_btn)
        self.range_row_widget = QtWidgets.QWidget()
        self.range_row_widget.setLayout(range_row)
        self.range_row_widget.setVisible(False)
        playback_layout.addWidget(self.range_row_widget)
        root.addWidget(playback_group)

        tracking_group = QtWidgets.QGroupBox("Rastreamento")
        tracking_layout = QtWidgets.QVBoxLayout(tracking_group)

        # De-shake strength - always visible (NOT inside the collapsible "Tracking Engine" section
        # below), matching pFX-Tracker's own layout where the Smooth strength slider sits outside
        # settings-section. Applied automatically right after every Track (see _apply_deshake) -
        # default 1 is deliberately lighter than the CEP tool's own UI default of 3, since even low
        # strength there was reported to erase small real movements.
        smooth_row = QtWidgets.QHBoxLayout()
        self.smooth_strength = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.smooth_strength.setRange(0, 10)
        self.smooth_strength.setValue(1)
        self.smooth_val_label = QtWidgets.QLabel("1")
        self.smooth_val_label.setFixedWidth(18)
        self.smooth_strength.valueChanged.connect(self._on_smooth_strength_changed)
        smooth_row.addWidget(QtWidgets.QLabel("Suavização"))
        smooth_row.addWidget(self.smooth_strength, 1)
        smooth_row.addWidget(self.smooth_val_label)
        tracking_layout.addLayout(smooth_row)

        # Simple 3-way button toggle instead of a dropdown - per user feedback, a combo box with
        # explanatory item text was more than this needed. Short tooltips carry the "from the
        # marked point" clarification instead of inline text.
        direction_row = QtWidgets.QHBoxLayout()
        direction_row.setSpacing(8)
        # Plain text, no arrow glyph glued onto it (per user feedback on the spacing that produced)
        # - equal-width buttons via the stretch factor below instead, so the row reads as one
        # consistent 3-way toggle rather than three oddly different-sized buttons.
        self.direction_both_btn = QtWidgets.QPushButton("Os 2 lados")
        self.direction_fwd_btn = QtWidgets.QPushButton("Pra frente")
        self.direction_back_btn = QtWidgets.QPushButton("Pra trás")
        self.direction_both_btn.setToolTip("A partir do ponto marcado, rastreia até o início E o fim do clipe.")
        self.direction_fwd_btn.setToolTip("A partir do ponto marcado, rastreia só até o FIM do clipe.")
        self.direction_back_btn.setToolTip("A partir do ponto marcado, rastreia só até o INÍCIO do clipe.")
        self.direction_group = QtWidgets.QButtonGroup(self)
        self.direction_group.setExclusive(True)
        for btn, value in ((self.direction_both_btn, "bidirectional"), (self.direction_fwd_btn, "forward"), (self.direction_back_btn, "backward")):
            btn.setCheckable(True)
            self.direction_group.addButton(btn)
            direction_row.addWidget(btn, 1)
        self.direction_both_btn.setChecked(True)
        tracking_layout.addLayout(direction_row)

        buttons_row = QtWidgets.QHBoxLayout()
        self.track_btn = QtWidgets.QPushButton("Track")
        self.track_btn.setObjectName("primary")
        self.track_btn.clicked.connect(self._on_track_clicked)
        self.cancel_btn = QtWidgets.QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        buttons_row.addWidget(self.track_btn, 1)
        buttons_row.addWidget(self.cancel_btn)
        tracking_layout.addLayout(buttons_row)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        tracking_layout.addWidget(self.progress_bar)

        # Advanced tracking-engine parameters - collapsed by default, matching pFX-Tracker's own
        # settings-section (only expanded when someone actually wants to tune the algorithm).
        self._DEFAULT_PARAMS = {"search": 200, "feature": 60, "conf": 40, "tol": 8}
        settings_header = QtWidgets.QPushButton("▸  Tracking Engine")
        settings_header.setCheckable(True)
        settings_header.setChecked(False)
        settings_header.setStyleSheet("text-align: left; background: #1d1d22; border: 1px solid #2c2c34;")
        settings_content = QtWidgets.QWidget()
        settings_content.setVisible(False)
        params_row = QtWidgets.QFormLayout(settings_content)
        self.search_diam = QtWidgets.QSpinBox()
        self.search_diam.setRange(20, 2000)
        self.feature_diam = QtWidgets.QSpinBox()
        self.feature_diam.setRange(10, 1000)
        self.conf_thresh = QtWidgets.QSpinBox()
        self.conf_thresh.setRange(0, 100)
        self.tol = QtWidgets.QSpinBox()
        self.tol.setRange(0, 60)
        reset_btn = QtWidgets.QPushButton("Resetar")
        reset_btn.clicked.connect(self._on_reset_params)
        self._reset_params_to_defaults()
        for spin in (self.search_diam, self.feature_diam, self.conf_thresh, self.tol):
            spin.valueChanged.connect(self._on_marker_params_changed)
        params_row.addRow("Raio de busca", self.search_diam)
        params_row.addRow("Raio da feature", self.feature_diam)
        params_row.addRow("Confiança mínima (%)", self.conf_thresh)
        params_row.addRow("Tolerância", self.tol)
        params_row.addRow("", reset_btn)

        def toggle_settings(checked: bool):
            settings_content.setVisible(checked)
            settings_header.setText(("▾" if checked else "▸") + "  Tracking Engine")
        settings_header.toggled.connect(toggle_settings)
        tracking_layout.addWidget(settings_header)
        tracking_layout.addWidget(settings_content)
        root.addWidget(tracking_group)

        apply_group = QtWidgets.QGroupBox("Prévia e aplicação")
        apply_group_layout = QtWidgets.QVBoxLayout(apply_group)

        # "Simulate result" preview - purely local (no Premiere writes at all), so the user can see
        # roughly what Stabilize/Apply Track will look like before committing to either. Stabilize
        # shifts the whole displayed frame to visually cancel the tracked shake; Follow overlays
        # a simple app-drawn reticle at the tracked point. Off by default; clicking the already-
        # active one turns preview off again (see _on_preview_mode_toggled).
        preview_row = QtWidgets.QHBoxLayout()
        preview_row.addWidget(QtWidgets.QLabel("Prévia:"))
        self.preview_stabilize_btn = QtWidgets.QPushButton("Stabilize")
        self.preview_follow_btn = QtWidgets.QPushButton("Seguir")
        for btn in (self.preview_stabilize_btn, self.preview_follow_btn):
            btn.setCheckable(True)
        self.preview_stabilize_btn.toggled.connect(lambda on: self._on_preview_mode_toggled("stabilize", on))
        self.preview_follow_btn.toggled.connect(lambda on: self._on_preview_mode_toggled("follow", on))
        preview_row.addWidget(self.preview_stabilize_btn)
        preview_row.addWidget(self.preview_follow_btn)
        preview_row.addStretch(1)
        apply_group_layout.addLayout(preview_row)
        self._preview_mode: str | None = None

        apply_row = QtWidgets.QHBoxLayout()
        self.mblur_check = QtWidgets.QCheckBox("Motion Blur")
        self.mblur_check.toggled.connect(self._on_mblur_toggled)
        self.mblur_180_btn = QtWidgets.QPushButton("180°")
        self.mblur_360_btn = QtWidgets.QPushButton("360°")
        for btn in (self.mblur_180_btn, self.mblur_360_btn):
            btn.setCheckable(True)
            btn.setEnabled(False)
        self.mblur_180_btn.setChecked(True)
        self.mblur_angle_group = QtWidgets.QButtonGroup(self)
        self.mblur_angle_group.setExclusive(True)
        self.mblur_angle_group.addButton(self.mblur_180_btn, 180)
        self.mblur_angle_group.addButton(self.mblur_360_btn, 360)
        self.stabilize_btn = QtWidgets.QPushButton("Stabilize")
        self.stabilize_btn.setToolTip("Remove o tremido do PRÓPRIO clipe rastreado.")
        self.stabilize_btn.clicked.connect(lambda: self._on_apply_clicked("stabilize"))
        # Renamed from "Apply Track" per user feedback - the old name didn't make clear that this
        # attaches a DIFFERENT, separately-selected object/clip so it follows the tracked point
        # (as opposed to Stabilize, which acts on the tracked clip itself).
        self.apply_track_btn = QtWidgets.QPushButton("Seguir Rastro")
        self.apply_track_btn.setObjectName("primary")
        self.apply_track_btn.setToolTip(
            "Anexa o clipe/objeto SELECIONADO na timeline pra seguir o ponto rastreado."
        )
        self.apply_track_btn.clicked.connect(lambda: self._on_apply_clicked("follow"))
        apply_row.addWidget(self.mblur_check)
        apply_row.addWidget(self.mblur_180_btn)
        apply_row.addWidget(self.mblur_360_btn)
        apply_row.addStretch(1)
        apply_row.addWidget(self.stabilize_btn)
        apply_row.addWidget(self.apply_track_btn)
        apply_group_layout.addLayout(apply_row)
        root.addWidget(apply_group)

        # TEMPORARY - isolated host test for the Nest-and-normalize step (TECHNICAL_PLAN.md's
        # Motion Tracker slice), before wiring it into the real Stabilize/Follow flow above. Remove
        # once confirmed working, alongside motracker.testNest/test_nest.
        test_nest_btn = QtWidgets.QPushButton("[TESTE] Nest + normalizar clipe selecionado")
        test_nest_btn.clicked.connect(self._on_test_nest_clicked)
        root.addWidget(test_nest_btn)

    def _reset_params_to_defaults(self):
        self.search_diam.setValue(self._DEFAULT_PARAMS["search"])
        self.feature_diam.setValue(self._DEFAULT_PARAMS["feature"])
        self.conf_thresh.setValue(self._DEFAULT_PARAMS["conf"])
        self.tol.setValue(self._DEFAULT_PARAMS["tol"])

    def _on_reset_params(self):
        self._reset_params_to_defaults()

    def _on_mblur_toggled(self, on: bool):
        self.mblur_180_btn.setEnabled(on)
        self.mblur_360_btn.setEnabled(on)

    def _on_preview_mode_toggled(self, mode: str, on: bool):
        other_btn = self.preview_follow_btn if mode == "stabilize" else self.preview_stabilize_btn
        if on:
            other_btn.blockSignals(True)
            other_btn.setChecked(False)
            other_btn.blockSignals(False)
            self._preview_mode = mode
            self.view.set_follow_overlay_visible(mode == "follow")
        else:
            if self._preview_mode == mode:
                self._preview_mode = None
            self.view.set_stabilize_offset(0, 0)
            self.view.set_follow_overlay_visible(False)
        # _refresh_marker() itself now knows to hide the marker whenever a preview is active (see
        # its own docstring) - single choke point, not repeated per call site.
        self._refresh_marker()
        self._apply_preview_for_frame(self._current_frame)

    def _apply_preview_for_frame(self, index: int):
        """Repositions whichever local "simulate result" preview is active for the given frame -
        no Premiere writes involved, purely visual (see FrameView.set_stabilize_offset/
        set_follow_overlay_visible's own docstrings)."""
        if self._preview_mode == "stabilize" and self._tracks:
            good = [t for t in self._tracks if t.get("conf", 0) > 0]
            current = self._tracks_by_frame.get(index)
            if good and current is not None:
                self.view.set_stabilize_offset(good[0]["x"] - current["x"], good[0]["y"] - current["y"])
            else:
                self.view.set_stabilize_offset(0, 0)
        elif self._preview_mode == "follow":
            current = self._tracks_by_frame.get(index)
            if current is not None:
                self.view.position_follow_overlay(current["x"], current["y"])

    def showEvent(self, event):
        super().showEvent(event)
        if not self._auto_loaded:
            self._auto_loaded = True
            self._on_load_clicked()

    def keyPressEvent(self, event):
        # Same shortcut set as pFX-Tracker's own (Space/←/→/I/O/Esc) - don't steal keys from a
        # focused text/number field, matching CEP's own guard.
        focus_widget = QtWidgets.QApplication.focusWidget()
        if isinstance(focus_widget, (QtWidgets.QLineEdit, QtWidgets.QSpinBox, QtWidgets.QAbstractSpinBox)):
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == QtCore.Qt.Key.Key_Escape:
            self._clear_range()
        elif key == QtCore.Qt.Key.Key_Space:
            self._toggle_playback()
        elif key == QtCore.Qt.Key.Key_Left:
            self._step_frame(-1)
        elif key == QtCore.Qt.Key.Key_Right:
            self._step_frame(1)
        elif key in (QtCore.Qt.Key.Key_I,):
            self._on_set_in_point()
        elif key in (QtCore.Qt.Key.Key_O,):
            self._on_set_out_point()
        else:
            super().keyPressEvent(event)

    # ---- frame loading (Stage 3) --------------------------------------------

    def load_frames(self, frames_dir: str, frame_count: int):
        self._frames_dir = Path(frames_dir)
        self._frame_count = frame_count
        self.scrubber.set_range(frame_count)
        self._show_frame(0)

    def _frame_path(self, index: int) -> Path:
        return self._frames_dir / f"{index + 1:06d}.jpg"

    def _show_frame(self, index: int):
        if self._frames_dir is None:
            return
        self._current_frame = index
        pixmap = QtGui.QPixmap(str(self._frame_path(index)))
        if not pixmap.isNull():
            self.view.set_frame(pixmap)
            self.view.fit_to_view()
        # Once a track exists, the point must follow it across frames - otherwise it just sits
        # wherever it was originally clicked, which is what the user's own real test caught.
        tracked = self._tracks_by_frame.get(index)
        if tracked is not None:
            self._syncing_point = True
            try:
                self.view.place_point(tracked["x"], tracked["y"])
            finally:
                self._syncing_point = False
        self._refresh_marker()
        self.scrubber.set_current_frame(index)
        self._apply_preview_for_frame(index)

    def _refresh_marker(self):
        # A single choke point for "should the marker be visible right now" - guarding every call
        # site individually (scrub, play, deshake, drag, track-finished, ...) turned out to be
        # exactly as fragile as it sounds: scrubbing/playing while a "simulate result" preview was
        # active kept bringing the marker back, since _show_frame's own _refresh_marker() call
        # didn't know about the preview state. update_marker() itself no-ops if there's no point
        # placed yet, so this is safe to call unconditionally from everywhere else.
        if self._preview_mode is not None:
            self.view.set_marker_visible(False)
            return
        tracked = self._tracks_by_frame.get(self._current_frame)
        dimmed = tracked is not None and tracked.get("conf", 1.0) <= 0
        edited = self._current_frame in self._edited_frames
        self.view.update_marker(self.search_diam.value() / 2, self.feature_diam.value() / 2, dimmed, edited)

    def _on_marker_params_changed(self, _value=None):
        self._refresh_marker()

    def _on_scrub(self, value: int):
        self._show_frame(value)

    # ---- playback (round 2) --------------------------------------------------

    def _step_frame(self, delta: int):
        self._stop_playback()
        if self._frame_count:
            self._show_frame(max(0, min(self._frame_count - 1, self._current_frame + delta)))

    def _toggle_playback(self):
        if self._playing:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        if not self._frame_count or self._frame_count < 2:
            return
        fps = float(self._clip_info.get("fps", 30)) if self._clip_info else 30.0
        self._playing = True
        self.play_btn.setIcon(self._pause_icon)
        self._play_timer.start(max(1, round(1000.0 / max(1.0, fps))))

    def _stop_playback(self):
        if self._playing:
            self._playing = False
            self.play_btn.setIcon(self._play_icon)
        self._play_timer.stop()

    def _on_play_tick(self):
        if self._current_frame >= self._frame_count - 1:
            self._stop_playback()
            return
        self._show_frame(self._current_frame + 1)

    # ---- In/Out range refinement (round 2) ------------------------------------

    def _on_set_in_point(self):
        # Toggle-off if already set on this exact frame, matching CEP's own I/O key behavior.
        if self._in_frame == self._current_frame:
            self._in_frame = -1
        else:
            self._in_frame = self._current_frame
            if self._out_frame < 0:
                self._out_frame = self._current_frame
        self._sync_range()

    def _on_set_out_point(self):
        if self._out_frame == self._current_frame:
            self._out_frame = -1
        else:
            self._out_frame = self._current_frame
            if self._in_frame < 0:
                self._in_frame = self._current_frame
        self._sync_range()

    def _on_range_changed(self, in_frame: int, out_frame: int):
        self._in_frame, self._out_frame = in_frame, out_frame
        self._update_range_actions()

    def _clear_range(self):
        self._in_frame = -1
        self._out_frame = -1
        self._sync_range()

    def _sync_range(self):
        lo, hi = min(self._in_frame, self._out_frame), max(self._in_frame, self._out_frame)
        self.scrubber.set_in_out(lo if self._in_frame >= 0 else -1, hi if self._out_frame >= 0 else -1)
        self._update_range_actions()

    def _update_range_actions(self):
        has_range = self._in_frame >= 0 and self._out_frame >= 0 and self._in_frame != self._out_frame
        self.range_row_widget.setVisible(has_range)
        if not has_range:
            return
        lo, hi = min(self._in_frame, self._out_frame), max(self._in_frame, self._out_frame)
        self.range_label.setText(f"{hi - lo + 1} frames (In→Out)")
        self.delete_range_btn.setEnabled(bool(self._tracks))
        self.retrack_range_btn.setEnabled(bool(self._tracks) and self.view.current_point() is not None)

    def _on_delete_range(self):
        if self._in_frame < 0 or self._out_frame < 0 or not self._tracks:
            return
        lo, hi = min(self._in_frame, self._out_frame), max(self._in_frame, self._out_frame)
        for collection in (self._tracks, self._tracks_raw):
            for t in collection:
                if lo <= t["frame"] <= hi:
                    t["conf"] = 0.0
        self._tracks_by_frame = {t["frame"]: t for t in self._tracks}
        self.view.set_path(self._tracks, self.conf_thresh.value() / 100.0)
        self.scrubber.set_tracks(self._tracks_by_frame, self._edited_frames)
        self._refresh_marker()
        self.status_label.setText(f"{hi - lo + 1} frames apagados.")

    def _on_retrack_range(self):
        if self._in_frame < 0 or self._out_frame < 0 or not self._tracks or self._frames_dir is None:
            return
        lo, hi = min(self._in_frame, self._out_frame), max(self._in_frame, self._out_frame)
        # Seed from the tracked point at lo, or the nearest earlier still-confident frame, or
        # finally the current point on the canvas - mirrors CEP's retrackRange exactly.
        seed = self._tracks_by_frame.get(lo)
        if seed is None or seed.get("conf", 0) <= 0:
            seed = None
            for f in range(lo - 1, -1, -1):
                candidate = self._tracks_by_frame.get(f)
                if candidate is not None and candidate.get("conf", 0) > 0:
                    seed = candidate
                    break
        if seed is None:
            point = self.view.current_point()
            if point is None:
                return
            seed = {"x": point[0], "y": point[1]}

        job = {
            "framesDir": str(self._frames_dir),
            "frameCount": self._frame_count,
            "startFrame": lo,
            "endFrame": hi,
            "direction": "forward",
            "point": {"x": seed["x"], "y": seed["y"]},
            "searchDiam": self.search_diam.value(),
            "featureDiam": self.feature_diam.value(),
            "confThresh": self.conf_thresh.value(),
            "tol": self.tol.value(),
        }
        self._retrack_thread = QtCore.QThread(self)
        self._retrack_worker = TrackWorker()
        self._retrack_worker.moveToThread(self._retrack_thread)
        self._retrack_thread.started.connect(lambda: self._retrack_worker.run(job))
        self._retrack_worker.finished.connect(self._on_retrack_finished)
        self._retrack_worker.failed.connect(self._on_track_failed)
        self._retrack_worker.finished.connect(self._retrack_thread.quit)
        self._retrack_worker.failed.connect(self._retrack_thread.quit)
        self._retrack_thread.finished.connect(self._retrack_thread.deleteLater)
        self.delete_range_btn.setEnabled(False)
        self.retrack_range_btn.setEnabled(False)
        self.status_label.setText(f"Retrackeando de {lo} a {hi}...")
        self._retrack_thread.start()

    def _on_retrack_finished(self, result: dict):
        # Merge the re-tracked span's frames into both the working and raw tracks, matching CEP's
        # own merge-by-frame behavior - frames outside the re-tracked range are untouched.
        by_frame_raw = {t["frame"]: t for t in self._tracks_raw}
        for t in result["tracks"]:
            by_frame_raw[t["frame"]] = dict(t)
        self._tracks_raw = [by_frame_raw[f] for f in sorted(by_frame_raw)]
        self._apply_deshake()
        self._update_range_actions()
        self.status_label.setText(f"Intervalo retrackeado: {len(result['tracks'])} frames.")

    # ---- tracking (Stage 4) --------------------------------------------------

    def _on_point_placed(self, x: float, y: float):
        self.status_label.setText(f"Ponto: {x:.1f}, {y:.1f} no frame {self._current_frame}")
        self._refresh_marker()

    def _on_point_dragged(self, x: float, y: float):
        # Fires on EVERY intermediate position during a live drag (Qt sends a steady stream of
        # these while the mouse moves) - a real host test found dragging felt broken/unresponsive
        # once this also rebuilt the whole path overlay (set_path) and repainted the entire
        # scrubber waveform (scrubber.set_tracks) on every single tick. Keep this handler CHEAP:
        # just the in-memory data write + a light marker reposition. The expensive redraws move to
        # _on_point_drag_finished, which only runs once when the mouse is actually released.
        if self._syncing_point:
            return
        entry = self._tracks_by_frame.get(self._current_frame)
        if entry is not None:
            entry["x"] = round(x, 2)
            entry["y"] = round(y, 2)
            entry["conf"] = 1.0
            self._edited_frames.add(self._current_frame)
        self._refresh_marker()
        self.status_label.setText(f"Ponto: {x:.1f}, {y:.1f} no frame {self._current_frame}")

    def _on_point_drag_finished(self):
        # The expensive part of a drag-to-correct, deferred here from _on_point_dragged (see its
        # own docstring) - runs once per drag gesture instead of once per mouse-move tick.
        entry = self._tracks_by_frame.get(self._current_frame)
        if entry is not None:
            # Also update the raw snapshot so a later de-shake re-tune treats this correction as
            # ground truth instead of silently overwriting it (see _tracks_raw's docstring).
            raw_entry = next((t for t in self._tracks_raw if t["frame"] == self._current_frame), None)
            if raw_entry is not None:
                raw_entry["x"], raw_entry["y"], raw_entry["conf"] = entry["x"], entry["y"], 1.0
            self.view.set_path(self._tracks, self.conf_thresh.value() / 100.0)
            self.scrubber.set_tracks(self._tracks_by_frame, self._edited_frames)

    def _on_track_clicked(self):
        point = self.view.current_point()
        if point is None or self._frames_dir is None:
            self.status_label.setText("Clique um ponto no frame antes de rastrear.")
            return
        self._stop_playback()
        self._clear_range()
        # The previous run's path/waveform/preview stayed on screen throughout a NEW track run,
        # only getting replaced once the whole thing finished - a real host test found this looked
        # like "the preview shown is from the OLD track, not the one running now". Clear it all
        # immediately so what's on screen always matches the run actually in progress (or a fresh
        # blank state while it computes).
        self._tracks = []
        self._tracks_by_frame = {}
        self._tracks_raw = []
        self._edited_frames.clear()
        self.view.set_path([], 0)
        self.scrubber.set_tracks({}, set())
        self._refresh_marker()
        self._apply_preview_for_frame(self._current_frame)

        if self.direction_fwd_btn.isChecked():
            direction = "forward"
        elif self.direction_back_btn.isChecked():
            direction = "backward"
        else:
            direction = "bidirectional"
        # Remembered so applyTrack can use THIS as its delta reference frame, not just whichever
        # frame ends up first after sorting trackData by frame number (wrong for bidirectional/
        # backward tracks - see index.js's own seedFrame comment for the full story).
        self._track_seed_frame = self._current_frame
        job = {
            "framesDir": str(self._frames_dir),
            "frameCount": self._frame_count,
            "startFrame": self._current_frame,
            "point": {"x": point[0], "y": point[1]},
            "searchDiam": self.search_diam.value(),
            "featureDiam": self.feature_diam.value(),
            "confThresh": self.conf_thresh.value(),
            "tol": self.tol.value(),
        }
        if direction == "bidirectional":
            job["bidirectional"] = True
        else:
            job["direction"] = direction
            job["endFrame"] = self._frame_count - 1 if direction == "forward" else 0

        self._track_thread = QtCore.QThread(self)
        self._track_worker = TrackWorker()
        self._track_worker.moveToThread(self._track_thread)
        self._track_thread.started.connect(lambda: self._track_worker.run(job))
        self._track_worker.progress.connect(self._on_track_progress)
        self._track_worker.finished.connect(self._on_track_finished)
        self._track_worker.failed.connect(self._on_track_failed)
        self._track_worker.finished.connect(self._track_thread.quit)
        self._track_worker.failed.connect(self._track_thread.quit)
        self._track_thread.finished.connect(self._track_thread.deleteLater)

        self.track_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self._last_live_nav_time = 0.0
        self._track_thread.start()

    def _on_cancel_clicked(self):
        if self._track_worker is not None:
            self._track_worker.cancel()

    def _on_track_progress(self, p: dict):
        self.progress_bar.setValue(int(p.get("pct", 0) * 100))
        # Live preview of the point while tracking runs, throttled to ~80ms like CEP's own
        # goToFrame(p.frame) in startTracking - moving the point/scrubbing on every single tracked
        # frame would repaint far more often than useful and could visibly lag the tracker thread.
        frame = p.get("frame")
        if frame is None or "x" not in p:
            return
        now = time.monotonic()
        if now - self._last_live_nav_time < 0.08:
            return
        self._last_live_nav_time = now
        # _tracks_by_frame has no entry for this frame yet (tracking is still in progress), so
        # _show_frame's own tracked-position snap finds nothing - place the point at the live
        # (x, y) explicitly afterward, same idea as CEP's own goToFrame(p.frame) during tracking.
        self._show_frame(frame)
        self._syncing_point = True
        try:
            self.view.place_point(p["x"], p["y"])
        finally:
            self._syncing_point = False
        # The tracking loop runs CPU-bound in a worker QThread and keeps emitting progress in a
        # tight burst - without this, the paint events _show_frame/place_point just scheduled sat
        # queued behind that burst and often never got a chance to actually flush to screen before
        # the whole track finished, which is why the live preview looked like nothing had changed
        # at all on a real host test. Forces this tick's pending repaints to happen NOW.
        QtWidgets.QApplication.processEvents()

    def _on_track_finished(self, result: dict):
        # A fresh track invalidates any prior manual edits/raw snapshot - this run's own raw output
        # becomes the new pristine base for de-shake (mirrors CEP's own S.smoothBase reset on
        # every (re)track).
        self._tracks_raw = [dict(t) for t in result["tracks"]]
        self._edited_frames.clear()
        self._log_raw_track_jitter()
        self._apply_deshake()
        self.track_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(100)
        QtCore.QTimer.singleShot(400, lambda: self.progress_bar.setVisible(False))
        self.status_label.setText(f"Track concluído: {len(self._tracks)} frames ({result.get('engine')})")

    def _log_raw_track_jitter(self):
        """Prints the RAW (zero de-shake, zero coordinate conversion) frame-to-frame deltas of
        this track to the companion's own log - direct evidence of whether CSRT/optical-flow
        tracking noise alone could explain a real host report of "slight but real" misalignment
        after Stabilize, instead of guessing from a subjective before/after slider comparison."""
        tracks = self._tracks_raw
        if len(tracks) < 2:
            return
        deltas = [
            (round(b["x"] - a["x"], 2), round(b["y"] - a["y"], 2))
            for a, b in zip(tracks, tracks[1:])
        ]
        max_dx = max(abs(d[0]) for d in deltas)
        max_dy = max(abs(d[1]) for d in deltas)
        avg_dx = sum(abs(d[0]) for d in deltas) / len(deltas)
        avg_dy = sum(abs(d[1]) for d in deltas) / len(deltas)
        print(
            f"[motracker] raw track jitter (extracted-frame pixels): "
            f"frames={len(tracks)} maxAbsDx={max_dx} maxAbsDy={max_dy} "
            f"avgAbsDx={round(avg_dx, 3)} avgAbsDy={round(avg_dy, 3)} "
            f"deltas={deltas}",
            flush=True,
        )

    def _apply_deshake(self):
        """Recompute the working (smoothed) track from the pristine raw snapshot at the current
        slider strength - always from _tracks_raw, never from the already-smoothed self._tracks,
        so re-tuning the slider back and forth never compounds. Called once automatically right
        after every Track (light default strength, no button click needed - see the smoothing
        slider in _build) and again whenever the slider value changes."""
        fps = float(self._clip_info.get("fps", 30)) if self._clip_info else 30.0
        self._tracks = tracker_engine.deshake_tracks(self._tracks_raw, self.smooth_strength.value(), fps)
        self._tracks_by_frame = {t["frame"]: t for t in self._tracks}
        self.view.set_path(self._tracks, self.conf_thresh.value() / 100.0)
        self.scrubber.set_tracks(self._tracks_by_frame, self._edited_frames)
        tracked = self._tracks_by_frame.get(self._current_frame)
        if tracked is not None:
            self._syncing_point = True
            try:
                self.view.place_point(tracked["x"], tracked["y"])
            finally:
                self._syncing_point = False
        self._refresh_marker()
        self._apply_preview_for_frame(self._current_frame)

    def _on_smooth_strength_changed(self, value: int):
        self.smooth_val_label.setText(str(value))
        if self._tracks_raw:
            self._apply_deshake()

    def _on_track_failed(self, message: str):
        self.track_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._update_range_actions()
        self.status_label.setText(f"Falhou: {message}")

    # ---- real Premiere connection (Stage 5) ----------------------------------

    def _on_load_clicked(self):
        if self._adapter is None:
            self.status_label.setText("Sem conexão com o companion.")
            return
        self.load_btn.setEnabled(False)
        self.status_label.setText("Lendo o clipe selecionado no Premiere...")
        info = self._adapter.get_clip_info()
        self.load_btn.setEnabled(True)
        if info is None:
            self.status_label.setText("Selecione um clipe de vídeo no Premiere e clique Recarregar.")
            return

        self._clip_info = info
        self.status_label.setText(f"Extraindo frames de \"{info.get('clipName', '')}\"...")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        motracker_dir = Path(__file__).resolve().parent
        self._extractor = FrameExtractor(motracker_dir)
        self._extractor.progress.connect(self._on_extract_progress)
        self._extractor.finished.connect(self._on_extract_finished)
        self._extractor.start(
            info["mediaPath"], info["srcRangeStart"], info["durationSec"], info["fps"],
        )

    def _on_extract_progress(self, p: dict):
        self.progress_bar.setValue(int(p.get("pct", 0) * 100))

    def _on_extract_finished(self, result: dict):
        if not result.get("ok"):
            self.status_label.setText(f"Falha ao extrair frames: {result.get('error')}")
            return
        # Printed (not just kept in-memory) so it's visible in the companion's own log without
        # another round trip - VFR (variable frame rate) source footage, common on phone cameras
        # under auto-exposure, has irregular real time between decoded frames; a constant assumed
        # fps for the frame-index-to-time conversion in applyTrack would then be systematically
        # wrong even with the true nominal fps, unlike a simple fps mismatch.
        frame_timestamps = result.get("frameTimestamps")
        print(
            f"[motracker] extraction finished: frameCount={result.get('frameCount')} "
            f"srcFps={result.get('srcFps')} vfrSuspect={result.get('vfrSuspect')} "
            f"realTimestamps={'yes (' + str(len(frame_timestamps)) + ')' if frame_timestamps else 'no'}",
            flush=True,
        )
        self._clip_info["extractedW"] = result.get("frameW", 0)
        self._clip_info["extractedH"] = result.get("frameH", 0)
        # Extraction no longer resamples to the SEQUENCE's fps (see build_ffmpeg_args's own
        # docstring for the real, user-confirmed root cause this fixes) - frames are now one per
        # REAL source frame, so the fps everything downstream (playback speed, de-shake's sample
        # rate, and critically the frame-index-to-time conversion when writing keyframes) must use
        # from here on is the SOURCE's own true rate, not the sequence's. Falls back to the
        # sequence fps only if ffmpeg's own sniff of the source's rate came back empty/invalid.
        src_fps = float(result.get("srcFps") or 0)
        if src_fps > 0:
            self._clip_info["fps"] = src_fps
        # Real per-frame timestamps (see extraction.py's own docstrings) - the authoritative way
        # to place each tracked frame's keyframe at its true elapsed time on VFR source, confirmed
        # via Premiere's own "Variable Frame Rate Detected" on a real host report. None when
        # showinfo's frame count didn't line up with what was actually extracted - applyTrack then
        # falls back to a constant fps, same as before this existed.
        self._clip_info["frameTimestamps"] = frame_timestamps
        self._tracks = []
        self._tracks_by_frame = {}
        self._tracks_raw = []
        self._edited_frames.clear()
        self.scrubber.set_tracks({}, set())
        self._clear_range()
        self._stop_playback()
        self.preview_stabilize_btn.setChecked(False)
        self.preview_follow_btn.setChecked(False)
        self._preview_mode = None
        self.view.set_stabilize_offset(0, 0)
        self.view.set_follow_overlay_visible(False)
        # A Reload previously left the old clip's tracked path (and its point/marker) drawn over
        # the freshly-loaded frames - clear both explicitly, not just the underlying track data.
        self.view.set_path([], 0)
        self.view.clear_point()
        self.load_frames(result["framesDir"], result["frameCount"])
        # extraction.py caps its own progress at 99% until ffmpeg's process actually exits (so the
        # bar never falsely claims "done" mid-process) - nothing ever pushed it the rest of the way
        # once it does, so it visibly stuck at 99% forever (cosmetic only, real host test confirmed
        # the extraction itself completed fine).
        self.progress_bar.setValue(100)
        QtCore.QTimer.singleShot(400, lambda: self.progress_bar.setVisible(False))
        self.status_label.setText(
            f"{result['frameCount']} frames extraídos - clique um ponto no clipe pra rastrear."
        )

    def _on_test_nest_clicked(self):
        # TEMPORARY - see the button's own comment in _build.
        if self._adapter is None:
            self.status_label.setText("Sem conexão com o companion.")
            return
        native_w = int((self._clip_info or {}).get("extractedW", 0))
        native_h = int((self._clip_info or {}).get("extractedH", 0))
        if native_w <= 0 or native_h <= 0:
            self.status_label.setText("[TESTE] Carregue um clipe primeiro (usa o tamanho nativo já extraído).")
            return
        self.status_label.setText(f"[TESTE] Testando Nest ({native_w}x{native_h}) no clipe selecionado no Premiere...")
        result = self._adapter.test_nest(native_w, native_h)
        if result is None:
            self.status_label.setText("[TESTE] Sem resposta do companion (não conectado?).")
        elif not result.get("ok"):
            self.status_label.setText(f"[TESTE] Falhou: {result.get('error')}")
        else:
            self.status_label.setText(f"[TESTE] OK - antes: {result.get('beforeName')} / clipe interno: {result.get('innerClipName')}")

    def _on_apply_clicked(self, mode: str):
        if self._adapter is None or self._clip_info is None:
            self.status_label.setText("Carregue um clipe primeiro (Load Clip).")
            return
        if not self._tracks:
            self.status_label.setText("Rode um Track antes de aplicar.")
            return

        info = self._clip_info
        request = {
            "mode": mode,
            "trackData": self._tracks,
            "fps": info["fps"],
            "seqW": info["frameW"],
            "seqH": info["frameH"],
            "extractedW": info.get("extractedW", 0),
            "extractedH": info.get("extractedH", 0),
            "mblurOn": self.mblur_check.isChecked(),
            "mblurAngle": self.mblur_angle_group.checkedId(),
            "trackIdx": info.get("trackIdx"),
            "clipStartTicks": info.get("clipStartTicks"),
            "seedFrame": self._track_seed_frame,
            "frameTimestamps": info.get("frameTimestamps"),
        }

        # The target's own native pixel size - used both for Nest-and-normalize (both modes now,
        # see TECHNICAL_PLAN.md's Motion Tracker slice) and for Follow's own coordinate scale
        # (mode != "stabilize" only, same as before). For Stabilize the target IS the tracked clip
        # itself, whose native size the extraction step already established (extractedW/H, now
        # always the clip's true native resolution - no separate lookup needed). For Follow the
        # target is a DIFFERENT clip, so its real size still comes from the companion's own
        # OpenCV read of its media file (get_follow_target_native_size's docstring explains why
        # that can't just be read from Premiere itself). Missing/unreadable falls back to the
        # sequence size inside applyTrack - not fatal, just the old less-accurate behaviour (and,
        # for Nest-and-normalize, simply skips nesting for that Apply).
        if mode == "stabilize":
            request["targetNativeW"] = info.get("extractedW", 0)
            request["targetNativeH"] = info.get("extractedH", 0)
        else:
            native_size = self._adapter.get_follow_target_native_size()
            if native_size:
                request["targetNativeW"] = native_size["width"]
                request["targetNativeH"] = native_size["height"]

        self.stabilize_btn.setEnabled(False)
        self.apply_track_btn.setEnabled(False)
        self.status_label.setText("Aplicando na timeline...")
        self._apply_timestamp = self._adapter.begin_apply_track(request)
        self._apply_poll_timer.start(150)

    def _poll_apply_track(self):
        if self._adapter is None or self._apply_timestamp is None:
            self._apply_poll_timer.stop()
            return
        status = self._adapter.poll_status(self._apply_timestamp)
        if status is None or not self._adapter.is_terminal(status):
            return
        self._apply_poll_timer.stop()
        self.stabilize_btn.setEnabled(True)
        self.apply_track_btn.setEnabled(True)
        if self._adapter.is_success(status):
            self.status_label.setText("Aplicado com sucesso na timeline.")
        else:
            self.status_label.setText(f"Falha ao aplicar: {status}")
