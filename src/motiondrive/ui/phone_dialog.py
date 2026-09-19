"""QR Code connection modal dialog for Phone Controller pairing."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QWidget, QLineEdit, QApplication,
                               QPlainTextEdit)

from motiondrive.logging_ import get_logger
from motiondrive.phone.server import PhoneControllerServer
from motiondrive.ui.theme import (COLOR_BG, COLOR_PANEL, COLOR_PANEL_ALT, COLOR_BORDER, COLOR_ACCENT,
                                   COLOR_TEXT_DIM, COLOR_GREEN)

log = get_logger(__name__)


class PhoneQRDialog(QDialog):
    """Modal dialog displaying QR codes and pairing status for Phone Controller in Single or Two Player modes."""

    def __init__(self, phone_server: PhoneControllerServer, parent=None):
        super().__init__(parent)
        self.phone_server = phone_server
        self.active_player_tab = 1

        # Initial player mode from settings if available, else default to "single"
        initial_mode = "single"
        if hasattr(self.phone_server, "parent") and self.phone_server.parent() is not None:
            p = self.phone_server.parent()
            if hasattr(p, "settings") and hasattr(p.settings, "player_mode"):
                initial_mode = getattr(p.settings, "player_mode", "single")
        self.player_mode = initial_mode if initial_mode in ("single", "two_player") else "single"

        self.setWindowTitle("MotionDrive Phone Controller")
        self.setFixedSize(540, 630)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLOR_BG}; border: 2px solid #1c2330; border-radius: 14px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)

        # Header
        header = QLabel("MOTIONDRIVE PHONE CONTROLLER")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 14px; font-weight: 800; letter-spacing: 2px; color: #e6edf3;")
        layout.addWidget(header)

        # Segmented Mode Selector (Single Player vs Two Player)
        mode_container = QWidget()
        mode_container.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 8px;")
        mode_layout = QHBoxLayout(mode_container)
        mode_layout.setContentsMargins(4, 4, 4, 4)
        mode_layout.setSpacing(4)

        self.btn_single = QPushButton("SINGLE PLAYER")
        self.btn_single.setFixedHeight(28)
        self.btn_single.clicked.connect(lambda: self._set_player_mode("single"))

        self.btn_two = QPushButton("TWO PLAYER")
        self.btn_two.setFixedHeight(28)
        self.btn_two.clicked.connect(lambda: self._set_player_mode("two_player"))

        mode_layout.addWidget(self.btn_single)
        mode_layout.addWidget(self.btn_two)
        layout.addWidget(mode_container)

        self.instruction = QLabel()
        self.instruction.setAlignment(Qt.AlignCenter)
        self.instruction.setWordWrap(True)
        self.instruction.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_DIM}; line-height: 1.3;")
        layout.addWidget(self.instruction)

        # Player Slot Tabs (Visible only in Two Player mode)
        self.tabs_widget = QWidget()
        tabs_row = QHBoxLayout(self.tabs_widget)
        tabs_row.setContentsMargins(0, 0, 0, 0)
        tabs_row.setSpacing(10)

        self.tab_p1 = QPushButton("PLAYER 1")
        self.tab_p1.setFixedSize(140, 32)
        self.tab_p1.clicked.connect(lambda: self._select_player_tab(1))

        self.tab_p2 = QPushButton("PLAYER 2")
        self.tab_p2.setFixedSize(140, 32)
        self.tab_p2.clicked.connect(lambda: self._select_player_tab(2))

        tabs_row.addStretch()
        tabs_row.addWidget(self.tab_p1)
        tabs_row.addWidget(self.tab_p2)
        tabs_row.addStretch()
        layout.addWidget(self.tabs_widget)

        # QR Code Display Container
        self.qr_container = QWidget()
        self.qr_container.setFixedSize(210, 210)
        self.qr_container.setStyleSheet(f"background-color: {COLOR_PANEL}; border-radius: 12px; border: 1px solid #21262d;")
        self.qr_layout = QVBoxLayout(self.qr_container)
        self.qr_layout.setContentsMargins(8, 8, 8, 8)

        self.qr_widgets = {
            1: _SVGWidget(self.phone_server.get_qr_svg(1)),
            2: _SVGWidget(self.phone_server.get_qr_svg(2)),
        }
        self.qr_widgets[2].hide()
        self.qr_layout.addWidget(self.qr_widgets[1])
        self.qr_layout.addWidget(self.qr_widgets[2])

        layout.addWidget(self.qr_container, alignment=Qt.AlignCenter)

        # Selectable URL + Copy Link Button row
        url_container = QWidget()
        url_layout = QHBoxLayout(url_container)
        url_layout.setContentsMargins(0, 0, 0, 0)
        url_layout.setSpacing(8)

        self.url_edit = QLineEdit(self.phone_server.get_local_url(1))
        self.url_edit.setReadOnly(True)
        self.url_edit.setToolTip("Click or select URL to copy manually")
        self.url_edit.setCursorPosition(0)
        self.url_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {COLOR_PANEL}; color: #3b9dff; font-family: monospace; "
            "font-size: 11px; padding: 6px 10px; border: 1px solid #21262d; border-radius: 6px; selection-background-color: #1f5fa8; }}"
        )

        self.copy_btn = QPushButton("COPY LINK")
        self.copy_btn.setFixedSize(94, 30)
        self.copy_btn.setStyleSheet(
            f"QPushButton {{ background-color: #21262d; color: #c9d1d9; font-size: 11px; font-weight: 700; "
            "border: 1px solid #30363d; border-radius: 6px; padding: 0; }} "
            "QPushButton:hover {{ background-color: #30363d; color: #ffffff; }}"
        )
        self.copy_btn.clicked.connect(self._copy_url_to_clipboard)

        url_layout.addWidget(self.url_edit, stretch=1)
        url_layout.addWidget(self.copy_btn)
        layout.addWidget(url_container)

        # Hotspot guidance note
        self.hotspot_note = QLabel()
        self.hotspot_note.setAlignment(Qt.AlignCenter)
        self.hotspot_note.setWordWrap(True)
        self.hotspot_note.setStyleSheet("font-size: 11px; color: #8b949e; background-color: #161b22; padding: 6px 10px; border-radius: 6px; border: 1px solid #21262d;")
        layout.addWidget(self.hotspot_note)

        # Status badge row
        status_row = QHBoxLayout()
        status_row.setSpacing(12)

        self.status_p1 = QLabel("P1: WAITING...")
        self.status_p1.setAlignment(Qt.AlignCenter)

        self.status_p2 = QLabel("P2: WAITING...")
        self.status_p2.setAlignment(Qt.AlignCenter)

        status_row.addStretch()
        status_row.addWidget(self.status_p1)
        status_row.addWidget(self.status_p2)
        status_row.addStretch()
        layout.addLayout(status_row)

        # 15-Second Firewall/Connection Troubleshooting Hint (initially hidden)
        self.troubleshoot_label = QLabel(
            "⚠️ <b>Still waiting?</b> Check if Windows Firewall is blocking MotionDrive, or verify PC is connected to your phone's Wi-Fi / Hotspot."
        )
        self.troubleshoot_label.setAlignment(Qt.AlignCenter)
        self.troubleshoot_label.setWordWrap(True)
        self.troubleshoot_label.setStyleSheet(
            "font-size: 11px; color: #d29922; background-color: #1e190a; padding: 6px 10px; "
            "border-radius: 6px; border: 1px solid #3d2e08;"
        )
        self.troubleshoot_label.setVisible(False)
        layout.addWidget(self.troubleshoot_label)

        layout.addStretch()

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(14)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedSize(120, 42)
        self.cancel_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_PANEL}; color: #e6edf3; border: 1px solid #30363d; "
            "border-radius: 10px; font-size: 13px; font-weight: 600; padding: 0 16px; }} "
            f"QPushButton:hover {{ border-color: {COLOR_ACCENT}; background-color: #1c2330; }}"
        )
        self.cancel_btn.clicked.connect(self.reject)

        self.start_btn = QPushButton("Start Driving")
        self.start_btn.setObjectName("primary")
        self.start_btn.setFixedSize(150, 42)
        self.start_btn.setStyleSheet(
            f"QPushButton#primary {{ background-color: {COLOR_ACCENT}; color: #051019; border: none; "
            "border-radius: 10px; font-size: 13px; font-weight: 700; padding: 0 16px; }} "
            "QPushButton#primary:hover {{ background-color: #5cb0ff; }} "
            "QPushButton#primary:disabled {{ background-color: #1c2734; color: #4a5568; }}"
        )
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.accept)

        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.start_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Connection Debug Toolbar Row
        debug_row = QHBoxLayout()
        debug_row.setSpacing(8)

        self.test_net_btn = QPushButton("RUN LOCAL TEST")
        self.test_net_btn.setFixedHeight(24)
        self.test_net_btn.setToolTip("Verifies desktop HTTP & WS server loopback ports only; does not verify physical phone reachability")
        self.test_net_btn.setStyleSheet(
            "QPushButton { background-color: #161b22; color: #8b949e; font-size: 10px; font-weight: 700; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 0 8px; } QPushButton:hover { color: #58a6ff; border-color: #1f6feb; }"
        )
        self.test_net_btn.clicked.connect(self._run_network_test)

        self.view_dbg_btn = QPushButton("VIEW DEBUG PANEL")
        self.view_dbg_btn.setFixedHeight(24)
        self.view_dbg_btn.setStyleSheet(
            "QPushButton { background-color: #161b22; color: #8b949e; font-size: 10px; font-weight: 700; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 0 8px; } QPushButton:hover { color: #58a6ff; border-color: #1f6feb; }"
        )
        self.view_dbg_btn.clicked.connect(self._open_debug_panel)

        self.copy_dbg_btn = QPushButton("COPY DEBUG INFO")
        self.copy_dbg_btn.setFixedHeight(24)
        self.copy_dbg_btn.setStyleSheet(
            "QPushButton { background-color: #161b22; color: #8b949e; font-size: 10px; font-weight: 700; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 0 8px; } QPushButton:hover { color: #58a6ff; border-color: #1f6feb; }"
        )
        self.copy_dbg_btn.clicked.connect(self._copy_debug_info)

        self.clear_dbg_btn = QPushButton("CLEAR LOG")
        self.clear_dbg_btn.setFixedHeight(24)
        self.clear_dbg_btn.setStyleSheet(
            "QPushButton { background-color: #161b22; color: #8b949e; font-size: 10px; font-weight: 700; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 0 8px; } QPushButton:hover { color: #f85149; border-color: #da3633; }"
        )
        self.clear_dbg_btn.clicked.connect(self._clear_debug_log)

        debug_row.addStretch()
        debug_row.addWidget(self.test_net_btn)
        debug_row.addWidget(self.view_dbg_btn)
        debug_row.addWidget(self.copy_dbg_btn)
        debug_row.addWidget(self.clear_dbg_btn)
        debug_row.addStretch()
        layout.addLayout(debug_row)

        # Wire server signals
        self.phone_server.phone_connected.connect(self._on_phone_connected)
        self.phone_server.phone_disconnected.connect(self._on_phone_disconnected)
        if hasattr(self.phone_server, "session_rejected"):
            self.phone_server.session_rejected.connect(self._on_session_rejected)

        # Initialize view mode UI
        self._apply_mode_ui()

        # 15-second timer for firewall / connection guidance
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._show_troubleshoot_hint)
        self._hint_timer.start(15000)

    def _on_session_rejected(self, client_ip: str, reason: str) -> None:
        log.warning("QR Dialog detected session rejection for %s: %s", client_ip, reason)
        self.troubleshoot_label.setText(f"⚠️ <b>Connection Rejected ({client_ip}):</b> {reason}")
        self.troubleshoot_label.setVisible(True)

    def _set_player_mode(self, mode: str) -> None:
        if self.player_mode == mode:
            return
        self.player_mode = mode
        if hasattr(self.phone_server, "parent") and self.phone_server.parent() is not None:
            p = self.phone_server.parent()
            if hasattr(p, "settings") and hasattr(p.settings, "player_mode"):
                p.settings.player_mode = mode
                if hasattr(p, "settings_store"):
                    p.settings_store.save(p.settings)
        self._apply_mode_ui()

    def _apply_mode_ui(self) -> None:
        if self.player_mode == "single":
            self.btn_single.setStyleSheet(
                "QPushButton { background-color: #1f6feb; color: #ffffff; font-size: 11px; font-weight: 800; "
                "border: none; border-radius: 6px; }"
            )
            self.btn_two.setStyleSheet(
                "QPushButton { background-color: transparent; color: #8b949e; font-size: 11px; font-weight: 700; "
                "border: none; border-radius: 6px; } QPushButton:hover { color: #c9d1d9; }"
            )
            self.instruction.setText("Scan QR code below with your phone on the same Wi-Fi or Hotspot.")
            self.tabs_widget.hide()
            self.hotspot_note.setText("📱 <b>Using Phone Hotspot?</b> Connect PC to hotspot and scan QR on host phone.")
            self.status_p2.hide()
            self.active_player_tab = 1
            self.qr_widgets[1].show()
            self.qr_widgets[2].hide()
            self.url_edit.setText(self.phone_server.get_local_url(1))
        else:
            self.btn_two.setStyleSheet(
                "QPushButton { background-color: #8957e5; color: #ffffff; font-size: 11px; font-weight: 800; "
                "border: none; border-radius: 6px; }"
            )
            self.btn_single.setStyleSheet(
                "QPushButton { background-color: transparent; color: #8b949e; font-size: 11px; font-weight: 700; "
                "border: none; border-radius: 6px; } QPushButton:hover { color: #c9d1d9; }"
            )
            self.instruction.setText("Scan QR code below with Phone 1 and Phone 2 on the same Wi-Fi or Hotspot.")
            self.tabs_widget.show()
            self.hotspot_note.setText("📱 <b>Using Phone Hotspot?</b> Connect PC to hotspot, scan Player 1 QR on host phone, scan Player 2 QR on second phone.")
            self.status_p2.show()
            self._select_player_tab(self.active_player_tab)

        self._update_status_badges()
        self._update_start_button()

    def _select_player_tab(self, player_id: int) -> None:
        self.active_player_tab = player_id
        if player_id == 1:
            self.tab_p1.setStyleSheet(
                f"QPushButton {{ background-color: #1f6feb; color: #ffffff; font-size: 11px; font-weight: 800; "
                "border: 1px solid #388bfd; border-radius: 6px; }}"
            )
            self.tab_p2.setStyleSheet(
                f"QPushButton {{ background-color: #21262d; color: #8b949e; font-size: 11px; font-weight: 700; "
                "border: 1px solid #30363d; border-radius: 6px; }}"
            )
            self.qr_widgets[1].show()
            self.qr_widgets[2].hide()
            self.url_edit.setText(self.phone_server.get_local_url(1))
        else:
            self.tab_p2.setStyleSheet(
                f"QPushButton {{ background-color: #8957e5; color: #ffffff; font-size: 11px; font-weight: 800; "
                "border: 1px solid #a371f7; border-radius: 6px; }}"
            )
            self.tab_p1.setStyleSheet(
                f"QPushButton {{ background-color: #21262d; color: #8b949e; font-size: 11px; font-weight: 700; "
                "border: 1px solid #30363d; border-radius: 6px; }}"
            )
            self.qr_widgets[2].show()
            self.qr_widgets[1].hide()
            self.url_edit.setText(self.phone_server.get_local_url(2))

    def _copy_url_to_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.phone_server.get_local_url(self.active_player_tab))
        self.copy_btn.setText("COPIED ✓")
        self.copy_btn.setStyleSheet(
            f"QPushButton {{ background-color: #1f6feb; color: #ffffff; font-size: 11px; font-weight: 700; "
            "border: 1px solid #388bfd; border-radius: 6px; padding: 0; }}"
        )
        QTimer.singleShot(2000, self._reset_copy_button)

    def _reset_copy_button(self) -> None:
        if hasattr(self, "copy_btn") and self.copy_btn is not None:
            self.copy_btn.setText("COPY LINK")
            self.copy_btn.setStyleSheet(
                f"QPushButton {{ background-color: #21262d; color: #c9d1d9; font-size: 11px; font-weight: 700; "
                "border: 1px solid #30363d; border-radius: 6px; padding: 0; }} "
                "QPushButton:hover {{ background-color: #30363d; color: #ffffff; }}"
            )

    def _show_troubleshoot_hint(self) -> None:
        if not self.start_btn.isEnabled():
            self.troubleshoot_label.setVisible(True)

    def _update_status_badges(self) -> None:
        p1_connected = self.phone_server.sessions[1].connected if 1 in self.phone_server.sessions else False
        p2_connected = self.phone_server.sessions[2].connected if 2 in self.phone_server.sessions else False

        if self.player_mode == "single":
            if p1_connected:
                self.status_p1.setText("PHONE: CONNECTED ✓")
                self.status_p1.setStyleSheet(
                    f"font-size: 11px; font-weight: 700; color: {COLOR_GREEN}; padding: 5px 14px; "
                    "background-color: #161b22; border-radius: 14px; border: 1px solid #238636;"
                )
            else:
                self.status_p1.setText("PHONE: WAITING...")
                self.status_p1.setStyleSheet(
                    "font-size: 11px; font-weight: 700; color: #e3b341; padding: 5px 14px; "
                    "background-color: #161b22; border-radius: 14px; border: 1px solid #30363d;"
                )
        else:
            if p1_connected:
                self.status_p1.setText("P1: CONNECTED ✓")
                self.status_p1.setStyleSheet(
                    f"font-size: 11px; font-weight: 700; color: {COLOR_GREEN}; padding: 5px 12px; "
                    "background-color: #161b22; border-radius: 14px; border: 1px solid #238636;"
                )
            else:
                self.status_p1.setText("P1: WAITING...")
                self.status_p1.setStyleSheet(
                    "font-size: 11px; font-weight: 700; color: #e3b341; padding: 5px 12px; "
                    "background-color: #161b22; border-radius: 14px; border: 1px solid #30363d;"
                )

            if p2_connected:
                self.status_p2.setText("P2: CONNECTED ✓")
                self.status_p2.setStyleSheet(
                    f"font-size: 11px; font-weight: 700; color: {COLOR_GREEN}; padding: 5px 12px; "
                    "background-color: #161b22; border-radius: 14px; border: 1px solid #238636;"
                )
            else:
                self.status_p2.setText("P2: WAITING...")
                self.status_p2.setStyleSheet(
                    "font-size: 11px; font-weight: 700; color: #e3b341; padding: 5px 12px; "
                    "background-color: #161b22; border-radius: 14px; border: 1px solid #30363d;"
                )

    def _update_start_button(self) -> None:
        p1_connected = self.phone_server.sessions[1].connected if 1 in self.phone_server.sessions else False
        self.start_btn.setEnabled(p1_connected)

    def _on_phone_connected(self, player_id: int, ip: str) -> None:
        log.info("PHONE STATUS UPDATE player=%d status=CONNECTED (client=%s)", player_id, ip)
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        dbg.log("PHONE_DIALOG", "PHONE_DIALOG_CONNECTED", player=player_id, client_ip=ip)
        dbg.log("PHONE_DIALOG", "PHONE_UI_STATE", player=player_id, state="CONNECTED")
        dbg.update_pipeline_stage(player_id, "ui", True, ip)

        self._hint_timer.stop()
        self.troubleshoot_label.setVisible(False)
        self._update_status_badges()
        self.start_btn.setEnabled(True)

    def _on_phone_disconnected(self, player_id: int) -> None:
        log.info("PHONE STATUS UPDATE player=%d status=DISCONNECTED", player_id)
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        dbg.log("PHONE_DIALOG", "PHONE_UI_STATE", player=player_id, state="DISCONNECTED")
        dbg.update_pipeline_stage(player_id, "ui", False)

        self._update_status_badges()
        self._update_start_button()

    def _run_network_test(self) -> None:
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        res = dbg.run_local_network_test(self.phone_server.HTTP_PORT, self.phone_server.active_ws_port)
        details_str = " | ".join(res.get("details", []))
        self.troubleshoot_label.setText(f"<b>DESKTOP LOCAL TEST:</b> {details_str}")
        self.troubleshoot_label.setVisible(True)

    def _open_debug_panel(self) -> None:
        viewer = PhoneDebugViewerDialog(self.phone_server, self.player_mode, self)
        viewer.exec()

    def _copy_debug_info(self) -> None:
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        report = dbg.get_sanitized_report(mode=self.player_mode, http_port=self.phone_server.HTTP_PORT, ws_port=self.phone_server.active_ws_port)
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(report)
        self.copy_dbg_btn.setText("COPIED ✓")
        QTimer.singleShot(2000, self._reset_debug_buttons)

    def _clear_debug_log(self) -> None:
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        dbg.clear_log()
        self.troubleshoot_label.setText("<b>Debug Log Cleared.</b> Ready for fresh connection test.")
        self.troubleshoot_label.setVisible(True)

    def _reset_debug_buttons(self) -> None:
        if hasattr(self, "copy_dbg_btn") and self.copy_dbg_btn is not None:
            self.copy_dbg_btn.setText("COPY DEBUG INFO")

    def showEvent(self, event):
        super().showEvent(event)
        self._update_status_badges()
        self._update_start_button()

    def closeEvent(self, event):
        self._hint_timer.stop()
        super().closeEvent(event)


class PhoneDebugViewerDialog(QDialog):
    """Auto-refreshing real-time network and phone connection debug viewer."""

    def __init__(self, phone_server: PhoneControllerServer, player_mode: str = "single", parent=None):
        super().__init__(parent)
        self.phone_server = phone_server
        self.player_mode = player_mode

        self.setWindowTitle("MotionDrive Phone Connection Debug Console")
        self.resize(720, 540)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLOR_BG}; border: 2px solid #1c2330; border-radius: 12px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Header
        header = QLabel("PHONE CONTROLLER DEBUG CONSOLE")
        header.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 1.5px; color: #58a6ff;")
        layout.addWidget(header)

        sub = QLabel("Real-time pipeline diagnostics, Windows firewall status & physical phone telemetry")
        sub.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_DIM};")
        layout.addWidget(sub)

        # Text display
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setStyleSheet(
            "QPlainTextEdit { background-color: #0b0e14; color: #7ee787; font-family: 'Consolas', 'Courier New', monospace; "
            "font-size: 11px; line-height: 1.4; border: 1px solid #21262d; border-radius: 8px; padding: 10px; }"
        )
        layout.addWidget(self.text_edit, stretch=1)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.copy_btn = QPushButton("COPY REPORT")
        self.copy_btn.setFixedHeight(30)
        self.copy_btn.setStyleSheet(
            "QPushButton { background-color: #161b22; color: #e6edf3; font-size: 11px; font-weight: 700; "
            "border: 1px solid #30363d; border-radius: 6px; padding: 0 14px; } QPushButton:hover { color: #58a6ff; border-color: #1f6feb; }"
        )
        self.copy_btn.clicked.connect(self._copy_report)

        self.clear_btn = QPushButton("CLEAR LOG")
        self.clear_btn.setFixedHeight(30)
        self.clear_btn.setStyleSheet(
            "QPushButton { background-color: #161b22; color: #e6edf3; font-size: 11px; font-weight: 700; "
            "border: 1px solid #30363d; border-radius: 6px; padding: 0 14px; } QPushButton:hover { color: #f85149; border-color: #da3633; }"
        )
        self.clear_btn.clicked.connect(self._clear_log)

        self.close_btn = QPushButton("CLOSE")
        self.close_btn.setFixedHeight(30)
        self.close_btn.setStyleSheet(
            "QPushButton { background-color: #21262d; color: #e6edf3; font-size: 11px; font-weight: 700; "
            "border: 1px solid #30363d; border-radius: 6px; padding: 0 16px; } QPushButton:hover { background-color: #30363d; }"
        )
        self.close_btn.clicked.connect(self.accept)

        btn_row.addWidget(self.copy_btn)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        # Auto-refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._update_report)
        self._refresh_timer.start(1000)

        self._update_report()

    def _update_report(self) -> None:
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        report = dbg.get_sanitized_report(
            mode=self.player_mode,
            http_port=self.phone_server.HTTP_PORT,
            ws_port=self.phone_server.active_ws_port
        )
        scrollbar = self.text_edit.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= (scrollbar.maximum() - 4)
        self.text_edit.setPlainText(report)
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _copy_report(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.text_edit.toPlainText())
        self.copy_btn.setText("COPIED ✓")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText("COPY REPORT") if hasattr(self, "copy_btn") else None)

    def _clear_log(self) -> None:
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        PhoneDebugLogger.get_instance().clear_log()
        self._update_report()

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)


class _SVGWidget(QWidget):
    """Simple PySide6 SVG widget for rendering SVG XML string."""

    def __init__(self, svg_xml: str, parent=None):
        super().__init__(parent)
        self._renderer = QSvgRenderer(svg_xml.encode("utf-8"))

    def paintEvent(self, event):
        painter = QPainter(self)
        self._renderer.render(painter, self.rect())
        painter.end()


class ControllerSourceDialog(QDialog):
    """Modal dialog asking user to choose between HANDS and PHONE controller."""

    def __init__(self, current_source: str = "hands", parent=None):
        super().__init__(parent)
        self.selected_source = current_source
        self.setWindowTitle("Choose Controller Source")
        self.setFixedSize(400, 300)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLOR_BG}; border: 2px solid #1c2330; border-radius: 14px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        header = QLabel("CHOOSE CONTROLLER SOURCE")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("font-size: 14px; font-weight: 800; letter-spacing: 2px; color: #e6edf3;")
        layout.addWidget(header)

        sub = QLabel("Select how you want to drive MotionDrive today")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_DIM};")
        layout.addWidget(sub)

        layout.addSpacing(6)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)

        self.hands_btn = QPushButton("🎥  HANDS\n\nCamera hand tracking")
        self.hands_btn.setFixedHeight(100)
        self.hands_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_PANEL}; color: #e6edf3; border: 2px solid #30363d; "
            "border-radius: 12px; font-size: 13px; font-weight: 700; padding: 12px; } "
            "QPushButton:hover { border-color: #3b9dff; background-color: #1c2330; }"
        )
        self.hands_btn.clicked.connect(lambda: self._select("hands"))

        self.phone_btn = QPushButton("📱  PHONE\n\nWireless phone wheel")
        self.phone_btn.setFixedHeight(100)
        self.phone_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_PANEL}; color: #e6edf3; border: 2px solid #30363d; "
            "border-radius: 12px; font-size: 13px; font-weight: 700; padding: 12px; } "
            "QPushButton:hover { border-color: #3b9dff; background-color: #1c2330; }"
        )
        self.phone_btn.clicked.connect(lambda: self._select("phone"))

        cards_row.addWidget(self.hands_btn)
        cards_row.addWidget(self.phone_btn)
        layout.addLayout(cards_row)

        layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(100, 32)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn, alignment=Qt.AlignCenter)

    def _select(self, source: str) -> None:
        self.selected_source = source
        self.accept()

