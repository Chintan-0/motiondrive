"""Small always-on-top floating camera monitor. Compact, draggable,
resizable, opacity-adjustable -- stays visible over a game window without
covering it (small default size, user can move/resize/hide it, position and
size persist across launches)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QRect, QTimer, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QImage, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizeGrip

from motiondrive.controller_state import TrackingState, TRACKING_LABELS
from motiondrive.hand_tracking import HandTracker, TrackingResult
from motiondrive.pedals import PedalState
from motiondrive.steering import SteeringState
from motiondrive.ui.theme import COLOR_BG, COLOR_ACCENT, COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_TEXT_DIM

_HAND_COLOR = {"Left": QColor("#3ddc84"), "Right": QColor("#3b9dff")}

_TRACKING_COLOR = {
    TrackingState.EXCELLENT: COLOR_GREEN,
    TrackingState.UNSTABLE: COLOR_YELLOW,
    TrackingState.RECOVERING: COLOR_YELLOW,
    TrackingState.RIGHT_HAND_LOST: COLOR_YELLOW,
    TrackingState.LEFT_HAND_LOST: COLOR_YELLOW,
    TrackingState.BOTH_HANDS_LOST: COLOR_RED,
}


class _OverlayCameraArea(QWidget):
    """Renders the mirrored frame + landmarks, toggle-able."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._tracking: TrackingResult | None = None
        self.show_landmarks = True
        self.setMinimumSize(120, 80)

    def update_frame(self, qimage: QImage, tracking) -> None:
        self._pixmap = QPixmap.fromImage(qimage)
        self._tracking = tracking
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        if self._pixmap is None or self._pixmap.isNull():
            painter.fillRect(rect, QColor(COLOR_BG))
            painter.setPen(QColor(COLOR_TEXT_DIM))
            painter.drawText(rect, Qt.AlignCenter, "No signal")
            painter.end()
            return

        scaled = self._pixmap.scaled(rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (rect.width() - scaled.width()) // 2
        y = (rect.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)

        if self.show_landmarks and self._tracking:
            sx = scaled.width() / max(1, self._pixmap.width())
            sy = scaled.height() / max(1, self._pixmap.height())
            for hand in (self._tracking.left, self._tracking.right):
                if hand is None:
                    continue
                color = _HAND_COLOR.get(hand.handedness, QColor(COLOR_ACCENT))
                pen = QPen(color, 1.5)
                painter.setPen(pen)
                pts = [(x + p.x * self._pixmap.width() * sx, y + p.y * self._pixmap.height() * sy)
                       for p in hand.landmarks]
                for a, b in HandTracker.HAND_CONNECTIONS_INDEX_PAIRS:
                    painter.drawLine(int(pts[a][0]), int(pts[a][1]), int(pts[b][0]), int(pts[b][1]))

        painter.end()


class FloatingCameraOverlay(QWidget):
    """Frameless always-on-top window. Drag the title strip to move; drag the
    bottom-right corner to resize; toolbar has Hide / Lock Position / gear
    (opens Settings) / compact-mode toggle."""

    settingsRequested = Signal()
    hideRequested = Signal()
    geometryChanged = Signal(int, int, int, int)  # x, y, width, height (debounced)
    compactToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.resize(260, 190)
        self._locked = False
        self._compact = False
        self._game_mode = False
        self._user_show_landmarks = True
        self._drag_offset: QPoint | None = None
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._emit_geometry_changed)

        self._steering_value = 0.0
        self._pedals = PedalState(0.0, 0.0)
        self._tracking_state = TrackingState.BOTH_HANDS_LOST

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self._title_bar = QWidget()
        self._title_bar.setFixedHeight(26)
        self._title_bar.setStyleSheet("background-color: #05080c;")
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(8, 0, 4, 0)

        self._live_dot = QLabel("● LIVE")
        self._live_dot.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 10px; font-weight: 700;")
        self._mode_title = QLabel("KEYBOARD")
        self._mode_title.setStyleSheet("color: #3b9dff; font-size: 10px; font-weight: 700; padding-left: 6px;")
        brand_lbl = QLabel("MOTIONDRIVE")
        brand_lbl.setStyleSheet("font-size: 10px; font-weight: 800; letter-spacing: 1px;")
        title_layout.addWidget(brand_lbl)
        title_layout.addWidget(self._mode_title)
        title_layout.addStretch()
        title_layout.addWidget(self._live_dot)

        self._compact_btn = QPushButton("▭")
        self._compact_btn.setFixedSize(22, 22)
        self._compact_btn.setToolTip("Compact Mode")
        self._compact_btn.clicked.connect(self._toggle_compact)
        self._lock_btn = QPushButton("🔓")
        self._lock_btn.setFixedSize(22, 22)
        self._lock_btn.setToolTip("Lock Position")
        self._lock_btn.clicked.connect(self._toggle_lock)
        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setFixedSize(22, 22)
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.clicked.connect(self.settingsRequested.emit)
        self._hide_btn = QPushButton("✕")
        self._hide_btn.setFixedSize(22, 22)
        self._hide_btn.setToolTip("Hide")
        self._hide_btn.clicked.connect(self.hideRequested.emit)
        for b in (self._compact_btn, self._lock_btn, self._settings_btn, self._hide_btn):
            b.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }"
                             "QPushButton:hover { color: #3b9dff; }")
            title_layout.addWidget(b)

        root.addWidget(self._title_bar)

        self.camera_area = _OverlayCameraArea()
        root.addWidget(self.camera_area, stretch=1)

        self._values_label = QLabel("STEER 0%   THROTTLE 0%   BRAKE 0%")
        self._values_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9px; padding: 2px 6px;")
        root.addWidget(self._values_label)

        self._perf_warning_label = QLabel("")
        self._perf_warning_label.setWordWrap(True)
        self._perf_warning_label.setStyleSheet(f"color: {COLOR_YELLOW}; font-size: 9px; font-weight: 700; padding: 0 6px;")
        self._perf_warning_label.setVisible(False)
        root.addWidget(self._perf_warning_label)

        self._focus_lost_label = QLabel("")
        self._focus_lost_label.setWordWrap(True)
        self._focus_lost_label.setStyleSheet(f"color: {COLOR_YELLOW}; font-size: 9px; font-weight: 700; padding: 0 6px;")
        self._focus_lost_label.setVisible(False)
        root.addWidget(self._focus_lost_label)

        self._target_label = QLabel("")
        self._target_label.setWordWrap(True)
        self._target_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9px; padding: 0 6px;")
        self._target_label.setVisible(False)
        root.addWidget(self._target_label)

        status_row = QHBoxLayout()
        self._input_label = QLabel("")
        self._input_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9px; padding: 0px 6px;")
        self._input_label.setVisible(False)
        root.addWidget(self._input_label)

        status_row.setContentsMargins(6, 2, 6, 4)
        self._status_label = QLabel("Tracking: hands not detected")
        self._status_label.setStyleSheet(f"color: {COLOR_RED}; font-size: 9px; font-weight: 700;")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        self._fps_label = QLabel("")
        self._fps_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 9px;")
        status_row.addWidget(self._fps_label)
        root.addLayout(status_row)

        grip = QSizeGrip(self)
        grip.setStyleSheet("background: transparent;")
        status_row.addWidget(grip, alignment=Qt.AlignBottom | Qt.AlignRight)

        self.setStyleSheet("background-color: #0d1117; border: 1px solid #2a3240;")

    def _toggle_lock(self) -> None:
        self._locked = not self._locked
        self._lock_btn.setText("🔒" if self._locked else "🔓")
        # Real OS-level click-through (WA_TransparentForMouseEvents on the
        # top-level window) would also swallow clicks on this very lock
        # button -- there'd be no way to unlock the overlay again once
        # click-through engaged. Genuinely solving that needs a custom
        # hit-test region, out of scope for this pass; Lock here still does
        # its original job of preventing accidental drags while gaming.

    def _toggle_compact(self) -> None:
        self.set_compact(not self._compact)
        self.compactToggled.emit(self._compact)

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.camera_area.setVisible(not compact)
        self._compact_btn.setText("▢" if compact else "▭")
        if compact:
            self.resize(self.width(), 60)
        else:
            self.resize(self.width(), max(190, self.width()))

    def apply_settings(self, *, opacity_percent: int, show_landmarks: bool,
                        show_labels: bool, show_fps: bool, show_wheel: bool) -> None:
        self.setWindowOpacity(max(0.2, min(1.0, opacity_percent / 100.0)))
        self._user_show_landmarks = show_landmarks
        self.camera_area.show_landmarks = show_landmarks and not self._game_mode
        self._status_label.setVisible(show_labels)
        self._fps_label.setVisible(show_fps)
        self._values_label.setVisible(show_wheel)

    def set_game_mode(self, active: bool) -> None:
        """Spec's 'Minimal Overlay' mode: while actually gaming, drop the
        full hand-skeleton rendering (still tracking underneath -- only the
        visualization is disabled) since it's pure wasted repaint work
        competing with the game for CPU, not something the player is
        looking at anyway once they've hidden MotionDrive."""
        self._game_mode = active
        self.camera_area.show_landmarks = getattr(self, "_user_show_landmarks", True) and not active

    def set_performance_sample(self, sample) -> None:
        if not self._game_mode:
            return  # normal mode already shows steering/throttle/brake via update_frame
        self._values_label.setText(
            f"Tracking {sample.tracking_fps:.0f} FPS")

    def set_performance_warning(self, active: bool) -> None:
        if active:
            self._perf_warning_label.setText("⚠ Low tracking performance -- try Performance Mode")
            self._perf_warning_label.setVisible(True)
        else:
            self._perf_warning_label.setVisible(False)

    def set_input_target_status(self, target: str, focused: bool) -> None:
        """Item 20's 'Target / Focus / Input' readout -- only meaningful in
        game mode, purely informational, never triggers any focus action
        itself."""
        if not self._game_mode or not target:
            self._target_label.setVisible(False)
            return
        mark = "✓" if focused else "✗"
        self._target_label.setText(f"Target: {target}    Focus: {mark}    Input: SendInput")
        self._target_label.setVisible(True)

    def set_focus_lost_warning(self, active: bool) -> None:
        """Non-modal, non-intrusive notice shown only while game_mode is
        active and the foreground window no longer matches the game/browser
        that was focused at Focus-Game/START & PLAY time. Never re-focuses
        anything itself -- purely informational, per the 'never steal focus
        during gameplay' requirement."""
        if active:
            self._focus_lost_label.setText("🟡 GAME FOCUS LOST -- click the game to resume")
            self._focus_lost_label.setVisible(True)
        else:
            self._focus_lost_label.setVisible(False)

    def set_input_mode(self, mode: str) -> None:
        text = "KEYBOARD" if mode == "keyboard" else "GAMEPAD"
        self._mode_title.setText(text)

    def update_frame(self, qimage: QImage, tracking, steering: SteeringState,
                      pedals: PedalState, fps: float) -> None:
        self.camera_area.update_frame(qimage, tracking)
        self._steering_value = steering.value
        self._pedals = pedals
        self._values_label.setText(
            f"STEER {steering.value * 100:+.0f}%   "
            f"THROTTLE {pedals.throttle * 100:.0f}%   "
            f"BRAKE {pedals.brake * 100:.0f}%"
        )
        self._fps_label.setText(f"{fps:.0f} FPS")

    def update_key_state(self, key_states: dict) -> None:
        self._input_label.setVisible(True)
        parts = []
        for name in ("left", "right", "accelerate", "brake"):
            key, is_down = key_states.get(name, ("-", False))
            parts.append(f"{key.upper()} {'●' if is_down else '○'}")
        self._input_label.setText("OUTPUT   " + "   ".join(parts))

    def clear_key_state(self) -> None:
        self._input_label.setVisible(False)

    def set_tracking_state(self, state: TrackingState) -> None:
        self._tracking_state = state
        self._status_label.setText(f"Tracking: {TRACKING_LABELS[state]}")
        color = _TRACKING_COLOR.get(state, COLOR_TEXT_DIM)
        self._status_label.setStyleSheet(f"color: {color}; font-size: 9px; font-weight: 700;")

    # ------------------------------------------------------------- dragging
    def mousePressEvent(self, event) -> None:
        if self._locked:
            return
        if event.position().toPoint().y() <= self._title_bar.height():
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._locked or self._drag_offset is None:
            return
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None

    # ------------------------------------------------------- persistence
    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._geometry_timer.start(500)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._geometry_timer.start(500)

    def _emit_geometry_changed(self) -> None:
        geo = self.geometry()
        self.geometryChanged.emit(geo.x(), geo.y(), geo.width(), geo.height())
