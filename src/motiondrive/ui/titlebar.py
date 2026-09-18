"""Custom dark title bar replacing native Windows chrome.

Buttons (Minimize, Maximize/Restore, Close) have dedicated widgets with real,
generous clickable areas (44x32px). Clicking/double-clicking caption buttons
never propagates to title-bar dragging or double-click window toggle. Title-bar
background dragging hands off to Qt's official QWindow.startSystemMove().
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QIcon, QPainter, QPen, QBrush, QColor
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton

from motiondrive.paths import resource_path
from motiondrive.ui.theme import COLOR_TEXT_DIM
from motiondrive.logging_ import get_logger

log = get_logger(__name__)


class _CaptionButton(QPushButton):
    """Minimize/maximize/restore/close button that draws its own vector glyph.

    Captures its own click and double-click events cleanly so mouse events on
    buttons never trigger title-bar window dragging or double-click maximize.
    """

    def __init__(self, kind: str, tooltip: str, danger: bool = False, parent=None):
        super().__init__("", parent)
        self._kind = kind  # "minimize" | "maximize" | "restore" | "close"
        self.setFixedSize(44, 32)
        self.setToolTip(tooltip)
        self.setCursor(Qt.ArrowCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._danger = danger
        self._hover_bg = QColor("#e81123" if danger else "#1c2330")
        self._icon_color = QColor(COLOR_TEXT_DIM)
        self._hover_icon_color = QColor("#ffffff") if danger else QColor("#e6edf3")
        self._hovering = False

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self.update()

    def enterEvent(self, event) -> None:
        self._hovering = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovering = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            event.accept()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            event.accept()  # Ignore double clicks on buttons so title-bar maximize is never triggered!

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#0a0e14")))
        painter.drawRect(rect)

        if self._hovering:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(self._hover_bg))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)

        color = self._hover_icon_color if self._hovering else self._icon_color
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        cx, cy = rect.center().x(), rect.center().y()

        if self._kind == "minimize":
            painter.drawLine(cx - 5, cy, cx + 5, cy)
        elif self._kind == "maximize":
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(cx - 5, cy - 5, 10, 10))
        elif self._kind == "restore":
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(cx - 3, cy - 5, 8, 8))
            painter.drawLine(cx - 5, cy - 3, cx - 5, cy + 3)
            painter.drawLine(cx - 5, cy + 3, cx + 3, cy + 3)
        elif self._kind == "close":
            painter.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
            painter.drawLine(cx - 5, cy + 5, cx + 5, cy - 5)
        painter.end()


class TitleBar(QWidget):
    minimizeRequested = Signal()
    maximizeRestoreRequested = Signal()
    closeRequested = Signal()

    HEIGHT = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "#titleBar { background-color: #0a0e14; border-bottom: 1px solid #1c2330; "
            "border-top-left-radius: 12px; border-top-right-radius: 12px; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(8)

        icon_path = resource_path("assets/icon.ico")
        icon_lbl = QLabel()
        if icon_path.exists():
            icon_lbl.setPixmap(QIcon(str(icon_path)).pixmap(16, 16))
        layout.addWidget(icon_lbl)

        title_lbl = QLabel("MotionDrive")
        title_lbl.setStyleSheet("font-size: 12px; font-weight: 700; letter-spacing: 0.5px;")
        layout.addWidget(title_lbl)

        self._page_lbl = QLabel("")
        self._page_lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_DIM}; padding-left: 6px;")
        layout.addWidget(self._page_lbl)

        layout.addStretch()

        self.min_btn = _CaptionButton("minimize", "Minimize")
        self.max_btn = _CaptionButton("maximize", "Maximize")
        self.close_btn = _CaptionButton("close", "Close", danger=True)

        self.min_btn.clicked.connect(self._on_min_clicked)
        self.max_btn.clicked.connect(self._on_max_clicked)
        self.close_btn.clicked.connect(self._on_close_clicked)

        for b in (self.min_btn, self.max_btn, self.close_btn):
            layout.addWidget(b)

    def _on_min_clicked(self) -> None:
        log.info("WINDOW MINIMIZE CLICK")
        self.minimizeRequested.emit()

    def _on_max_clicked(self) -> None:
        win = self.window()
        if win and win.isMaximized():
            log.info("WINDOW RESTORE CLICK")
        else:
            log.info("WINDOW MAXIMIZE CLICK")
        self.maximizeRestoreRequested.emit()

    def _on_close_clicked(self) -> None:
        log.info("WINDOW CLOSE CLICK")
        self.closeRequested.emit()

    def set_page_name(self, name: str) -> None:
        self._page_lbl.setText(f"›  {name}" if name else "")

    def set_maximized(self, maximized: bool) -> None:
        self.max_btn.set_kind("restore" if maximized else "maximize")
        self.max_btn.setToolTip("Restore" if maximized else "Maximize")

    def _is_over_button(self, pos) -> bool:
        child = self.childAt(pos)
        if child is None:
            return False
        return isinstance(child, _CaptionButton) or child in (self.min_btn, self.max_btn, self.close_btn)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._is_over_button(event.pos()):
                event.ignore()
                return
            window = self.window()
            handle = window.windowHandle() if window is not None else None
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._is_over_button(event.pos()):
                event.ignore()
                return
            window = self.window()
            if window is not None:
                if window.isMaximized():
                    window.showNormal()
                else:
                    window.showMaximized()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
