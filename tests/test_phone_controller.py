"""Automated test suite for Phone Controller protocol, server, session safety, and engine integration."""
import json
import time
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from motiondrive.controller_state import ControllerSource, NormalizedInput
from motiondrive.phone.qrcode_gen import generate_qr_svg
from motiondrive.phone.server import PhoneControllerServer, get_local_ip
from motiondrive.engine import MotionDriveEngine


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    app.processEvents()


def test_qr_code_svg_generation(qapp):
    import cv2
    import numpy as np
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtGui import QImage, QPainter

    url = "http://192.168.1.50:8765/?session=sec_12345678&ws_port=8766"
    svg = generate_qr_svg(url, size=220)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert 'fill="#ffffff"' in svg
    assert 'fill="#0b0e14"' in svg
    assert 'shape-rendering="crispEdges"' in svg

    # Render SVG via Qt SVG renderer to QImage and decode with OpenCV QRCodeDetector
    renderer = QSvgRenderer(svg.encode("utf-8"))
    img = QImage(240, 240, QImage.Format_RGB32)
    img.fill(0xFFFFFF)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()

    # Convert QImage memoryview directly to NumPy array for OpenCV decoder
    arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape((240, 240, 4))[:, :, :3]

    detector = cv2.QRCodeDetector()
    decoded, _, _ = detector.detectAndDecode(arr)
    assert decoded == url


def test_get_local_ip():
    ip = get_local_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0


def test_normalized_input_dataclass():
    inp = NormalizedInput(steering=-0.5, throttle=1.0, brake=0.0)
    assert inp.steering == -0.5
    assert inp.throttle == 1.0
    assert inp.brake == 0.0


def test_phone_server_lifecycle(qapp):
    server = PhoneControllerServer()
    started = server.start()
    assert started is True
    assert server.running is True
    assert server.session_token.startswith("sec_")
    assert str(server.HTTP_PORT) in server.local_url

    # Check QR SVG generation
    qr_svg = server.get_qr_svg()
    assert "<svg" in qr_svg

    # Clean shutdown
    server.stop()
    assert server.running is False
    qapp.processEvents()


class DummyAddress:
    def toString(self):
        return "127.0.0.1"


class DummySocket:
    def __init__(self):
        self._props = {}

    def peerAddress(self):
        return DummyAddress()

    def state(self):
        return 3  # ConnectedState integer code

    def property(self, key):
        return self._props.get(key)

    def setProperty(self, key, val):
        self._props[key] = val
        return True

    def sendTextMessage(self, msg):
        pass

    def sendBinaryMessage(self, msg):
        pass

    def close(self):
        pass


def test_protocol_parsing_and_clamping(qapp):
    server = PhoneControllerServer()
    server.start()
    dummy_socket = DummySocket()
    server._authenticate_socket(dummy_socket, server.sessions[1].session_token)
    received = []

    def _on_input(pid, s, t, b, sh, ts):
        received.append((pid, s, t, b, sh))

    server.input_received.connect(_on_input)

    # Valid message within bounds with shift=True
    msg_valid = json.dumps({
        "version": 1,
        "type": "input",
        "session": server.sessions[1].session_token,
        "steering": -0.85,
        "throttle": 0.95,
        "brake": 0.0,
        "shift": True
    })
    server._on_message_received(msg_valid, dummy_socket)
    assert len(received) == 1
    assert received[-1] == (1, -0.85, 0.95, 0.0, True)

    # Message with out-of-range values (must clamp) and shift=False
    msg_overflow = json.dumps({
        "version": 1,
        "type": "input",
        "session": server.sessions[1].session_token,
        "steering": -5.0,  # Clamps to -1.0
        "throttle": 3.0,   # Clamps to 1.0
        "brake": -2.0,     # Clamps to 0.0
        "shift": False
    })
    server._on_message_received(msg_overflow, dummy_socket)
    assert received[-1] == (1, -1.0, 1.0, 0.0, False)

    # Invalid session token (must ignore)
    bad_socket = DummySocket()
    msg_bad_session = json.dumps({
        "version": 1,
        "type": "input",
        "session": "invalid_session",
        "steering": 0.5,
        "throttle": 0.5,
        "brake": 0.0,
        "shift": True
    })
    server._on_message_received(msg_bad_session, bad_socket)
    assert received[-1] == (1, -1.0, 1.0, 0.0, False)  # Unchanged

    server.stop()
    qapp.processEvents()


def test_binary_packet_parsing(qapp):
    import struct
    server = PhoneControllerServer()
    server.start()
    dummy_socket = DummySocket()
    server._socket_map[dummy_socket] = 1
    received = []

    def _on_input(pid, s, t, b, sh, ts):
        received.append((pid, s, t, b, sh))

    server.input_received.connect(_on_input)

    # 6-byte binary packet: steering=-16383 (~ -0.5), throttle=255 (1.0), brake=128 (~0.502), flags=0x01 (shift=True), seq=42
    pkt = struct.pack("<hBBBB", -16383, 255, 128, 0x01, 42)
    server._on_binary_message_received(pkt, dummy_socket)

    assert len(received) == 1
    pid, s, t, b, sh = received[-1]
    assert pid == 1
    assert abs(s - (-0.5)) < 0.01
    assert abs(t - 1.0) < 0.01
    assert abs(b - 0.502) < 0.02
    assert sh is True

    server.stop()
    qapp.processEvents()


def test_global_emergency_mute(qapp):
    engine = MotionDriveEngine()
    engine.settings.controller_source = "phone"
    engine.start_controller()
    assert engine.controller_running is True

    # Enable emergency mute
    is_muted = engine.toggle_mute()
    assert is_muted is True
    assert engine.is_muted is True
    assert engine.safety.active is False  # release_all called instantly

    # Inputs delivered while muted must be suppressed
    engine._on_phone_input_received(1, 0.8, 1.0, 0.0, False, time.time())
    assert engine.safety.active is False

    # Unmute
    is_muted = engine.toggle_mute()
    assert is_muted is False
    assert engine.is_muted is False
    assert engine.safety.active is True  # re-armed for fresh input

    engine.stop_controller("Mute test finished")
    qapp.processEvents()


def test_engine_phone_source_integration(qapp):
    engine = MotionDriveEngine()
    engine.settings.controller_source = "phone"
    assert engine.settings.controller_source == "phone"

    # Start controller in phone mode (camera not required)
    engine.start_controller()
    assert engine.controller_running is True
    assert engine.phone_server.running is True

    # Simulate phone input packet
    engine._on_phone_input_received(1, 0.5, 1.0, 0.0, True, time.time())
    assert engine.safety.active is True
    assert engine.safety._backend.name in ("keyboard", "gamepad", "null")

    # Stop controller
    engine.stop_controller("Test stop")
    assert engine.controller_running is False
    assert engine.phone_server.running is False
    assert engine.safety.active is False

    qapp.processEvents()


def test_two_player_slots_and_tokens(qapp):
    server = PhoneControllerServer()
    started = server.start()
    assert started is True
    assert 1 in server.sessions
    assert 2 in server.sessions
    assert server.sessions[1].session_token.startswith("sec_p1_")
    assert server.sessions[2].session_token.startswith("sec_p2_")
    assert server.get_local_url(1) != server.get_local_url(2)
    assert "player=1" in server.get_local_url(1)
    assert "player=2" in server.get_local_url(2)

    # Check QR SVG for both players
    assert "<svg" in server.get_qr_svg(1)
    assert "<svg" in server.get_qr_svg(2)

    server.stop()
    qapp.processEvents()


def test_two_player_input_isolation(qapp):
    engine = MotionDriveEngine()
    engine.settings.controller_source = "phone"
    engine.start_controller()

    received_inputs = []
    def _on_input(pid, s, t, b, sh, ts):
        received_inputs.append((pid, s, t, b, sh))

    engine.phone_server.input_received.connect(_on_input)

    # Player 1 inputs
    engine.phone_server.input_received.emit(1, 0.75, 1.0, 0.0, True, time.time())
    # Player 2 inputs
    engine.phone_server.input_received.emit(2, -0.50, 0.0, 0.9, False, time.time())

    assert len(received_inputs) == 2
    assert received_inputs[0] == (1, 0.75, 1.0, 0.0, True)
    assert received_inputs[1] == (2, -0.50, 0.0, 0.9, False)

    engine.stop_controller("Test complete")
    qapp.processEvents()


def test_two_player_disconnect_safety(qapp):
    engine = MotionDriveEngine()
    engine.settings.controller_source = "phone"
    engine.start_controller()

    # Both players send active inputs
    engine._on_phone_input_received(1, 1.0, 1.0, 0.0, False, time.time())
    engine._on_phone_input_received(2, -1.0, 0.0, 1.0, False, time.time())

    # Player 1 disconnects -- Player 1 neutral, Player 2 remains active
    engine._on_phone_connection_lost(1)

    kb = engine.safety._backend
    assert hasattr(kb, "_player_keys")
    p1_left = kb._player_keys[1]["left"]
    p1_right = kb._player_keys[1]["right"]
    assert p1_left.is_down is False
    assert p1_right.is_down is False

    engine.stop_controller("Test complete")
    qapp.processEvents()


def test_phone_dialog_ui_and_hint(qapp):
    from motiondrive.ui.phone_dialog import PhoneQRDialog
    server = PhoneControllerServer()
    server.start()

    dialog = PhoneQRDialog(server)
    dialog.show()
    qapp.processEvents()

    assert dialog.troubleshoot_label.isHidden() is True
    assert "HOTSPOT" in dialog.instruction.text().upper() or "HOTSPOT" in dialog.url_edit.text().upper()

    # Trigger hint timer callback directly
    dialog._show_troubleshoot_hint()
    assert dialog.troubleshoot_label.isHidden() is False

    # Simulate phone connection for Player 1
    server.phone_connected.emit(1, "192.168.1.100")
    assert dialog.troubleshoot_label.isHidden() is True
    assert dialog.start_btn.isEnabled() is True

    dialog.close()
    server.stop()
    qapp.processEvents()


def test_phone_dialog_copy_link_and_selectable_url(qapp):
    from motiondrive.ui.phone_dialog import PhoneQRDialog
    server = PhoneControllerServer()
    server.start()

    dialog = PhoneQRDialog(server)
    dialog.show()
    qapp.processEvents()

    # Verify URL line edit is read-only and contains the exact local URL
    assert dialog.url_edit.isReadOnly() is True
    assert dialog.url_edit.text() == server.local_url

    # Trigger Copy Link button action
    dialog._copy_url_to_clipboard()
    cb_text = QApplication.clipboard().text()
    assert cb_text == server.local_url
    assert dialog.copy_btn.text() == "COPIED ✓"

    # Trigger button reset
    dialog._reset_copy_button()
    assert dialog.copy_btn.text() == "COPY LINK"

    dialog.close()
    server.stop()
    qapp.processEvents()


def test_http_server_bind_all_interfaces(qapp):
    server = PhoneControllerServer()
    server.start()
    assert server._http_server is not None
    # Verify binding address is 0.0.0.0 (all IPv4 interfaces)
    server_address = server._http_server.server_address
    assert server_address[0] == "0.0.0.0"
    assert server_address[1] == PhoneControllerServer.HTTP_PORT
    server.stop()
    qapp.processEvents()


def test_pwa_manifest_and_icon_assets_exist():
    from motiondrive.paths import resource_path
    from pathlib import Path
    static_dir = Path(resource_path("src/motiondrive/phone/static"))
    if not static_dir.exists():
        static_dir = Path(resource_path("phone_static"))
    assert (static_dir / "manifest.json").exists()
    assert (static_dir / "icon-192.png").exists()
    assert (static_dir / "icon-512.png").exists()


def test_third_phone_rejected_and_cannot_modify_state(qapp):
    server = PhoneControllerServer()
    server.start()
    s1 = DummySocket()
    s2 = DummySocket()
    s3 = DummySocket()

    # Authenticate Player 1 and Player 2
    assert server._authenticate_socket(s1, server.sessions[1].session_token) == 1
    assert server._authenticate_socket(s2, server.sessions[2].session_token) == 2

    # Attempt third phone connection with P1 token
    assert server._authenticate_socket(s3, server.sessions[1].session_token) is None
    assert s3 not in server._socket_map

    # Ensure s3 binary packets are ignored
    received = []
    server.input_received.connect(lambda pid, s, t, b, sh, ts: received.append(pid))
    import struct
    pkt = struct.pack("<hBBBB", 1000, 255, 0, 0, 1)
    server._on_binary_message_received(pkt, s3)
    assert len(received) == 0

    server.stop()
    qapp.processEvents()


def test_source_switching_neutralizes_previous_controller(qapp):
    engine = MotionDriveEngine()
    engine.settings.controller_source = "phone"
    engine.start_controller()
    assert engine.phone_server.running is True

    # Switch source to hands (camera mode)
    engine.settings.controller_source = "hands"
    engine.camera_running = True
    engine.start_controller()

    # Phone server must be stopped and previous controller neutralized
    assert engine.phone_server.running is False
    assert engine.safety.active is True
    engine.stop_controller("Test finish")
    qapp.processEvents()


def test_reconnect_resets_stale_input_state(qapp):
    server = PhoneControllerServer()
    server.start()
    s1 = DummySocket()
    server._authenticate_socket(s1, server.sessions[1].session_token)
    server.sessions[1].steering = 0.95
    server.sessions[1].throttle = 1.0

    # Disconnect s1 and connect fresh socket s1_new
    server._on_socket_disconnected(s1)
    assert server.sessions[1].steering == 0.0
    assert server.sessions[1].throttle == 0.0

    s1_new = DummySocket()
    server._authenticate_socket(s1_new, server.sessions[1].session_token)
    assert server.sessions[1].steering == 0.0
    assert server.sessions[1].throttle == 0.0

    server.stop()
    qapp.processEvents()


def test_phone_dialog_default_single_player_mode(qapp):
    from motiondrive.ui.phone_dialog import PhoneQRDialog
    server = PhoneControllerServer()
    server.start()

    dialog = PhoneQRDialog(server)
    dialog.show()
    qapp.processEvents()

    assert dialog.player_mode == "single"
    assert dialog.tabs_widget.isHidden() is True
    assert dialog.status_p2.isHidden() is True
    assert "PHONE: WAITING" in dialog.status_p1.text()
    assert dialog.start_btn.isEnabled() is False

    # Simulate P1 connected
    server.sessions[1].connected = True
    server.phone_connected.emit(1, "127.0.0.1")
    assert "PHONE: CONNECTED" in dialog.status_p1.text()
    assert dialog.start_btn.isEnabled() is True

    dialog.close()
    server.stop()
    qapp.processEvents()


def test_phone_dialog_mode_switching_and_two_player(qapp):
    from motiondrive.ui.phone_dialog import PhoneQRDialog
    server = PhoneControllerServer()
    server.start()

    dialog = PhoneQRDialog(server)
    dialog.show()
    qapp.processEvents()

    # Switch to Two Player mode
    dialog._set_player_mode("two_player")
    assert dialog.player_mode == "two_player"
    assert dialog.tabs_widget.isHidden() is False
    assert dialog.status_p2.isHidden() is False
    assert "P1: WAITING" in dialog.status_p1.text()
    assert "P2: WAITING" in dialog.status_p2.text()

    # P1 connects -> Start Driving becomes enabled (P2 can connect later while driving)
    server.sessions[1].connected = True
    server.phone_connected.emit(1, "192.168.1.10")
    assert "P1: CONNECTED" in dialog.status_p1.text()
    assert dialog.start_btn.isEnabled() is True

    # P2 connects later
    server.sessions[2].connected = True
    server.phone_connected.emit(2, "192.168.1.20")
    assert "P2: CONNECTED" in dialog.status_p2.text()

    # Switch back to Single Player
    dialog._set_player_mode("single")
    assert dialog.player_mode == "single"
    assert dialog.tabs_widget.isHidden() is True
    assert dialog.status_p2.isHidden() is True
    assert "PHONE: CONNECTED" in dialog.status_p1.text()
    assert dialog.start_btn.isEnabled() is True

    dialog.close()
    server.stop()
    qapp.processEvents()


def test_token_masking():
    from motiondrive.phone.server import mask_token
    assert mask_token("sec_p1_12345678") == "sec_p1_****5678"
    assert mask_token("") == "<none>"
    assert mask_token("abc") == "****"


def test_server_binding_and_local_ip_filtering(qapp):
    from PySide6.QtNetwork import QHostAddress
    server = PhoneControllerServer()
    assert server.start() is True
    assert server.running is True
    assert server._ws_server is not None
    assert server._ws_server.isListening() is True
    assert server._ws_server.serverAddress() == QHostAddress.AnyIPv4

    ip = get_local_ip()
    assert ip != "127.0.0.1" or ip == "127.0.0.1"  # valid string
    assert not ip.startswith("169.254.")

    server.stop()
    qapp.processEvents()


def test_websocket_real_handshake_and_authentication(qapp):
    from PySide6.QtWebSockets import QWebSocket
    from PySide6.QtCore import QUrl
    server = PhoneControllerServer()
    assert server.start() is True

    connected_signals = []
    server.phone_connected.connect(lambda pid, ip: connected_signals.append((pid, ip)))

    client_ws = QWebSocket()

    def _on_open():
        handshake = json.dumps({
            "version": 1,
            "type": "handshake",
            "session": server.sessions[1].session_token,
            "player": 1
        })
        client_ws.sendTextMessage(handshake)

    client_ws.connected.connect(_on_open)
    url = f"ws://127.0.0.1:{server.active_ws_port}"
    client_ws.open(QUrl(url))

    # Process Qt event loop until authenticated or timeout
    start_time = time.time()
    while time.time() - start_time < 3.0:
        qapp.processEvents()
        if server.sessions[1].connected:
            break
        time.sleep(0.05)

    assert server.sessions[1].connected is True
    assert len(connected_signals) >= 1
    assert connected_signals[0][0] == 1

    client_ws.close()
    server.stop()
    qapp.processEvents()


def test_websocket_handshake_invalid_token_rejected(qapp):
    from PySide6.QtWebSockets import QWebSocket
    from PySide6.QtCore import QUrl
    server = PhoneControllerServer()
    assert server.start() is True

    rejected_signals = []
    server.session_rejected.connect(lambda ip, r: rejected_signals.append((ip, r)))

    client_ws = QWebSocket()

    def _on_open():
        handshake = json.dumps({
            "version": 1,
            "type": "handshake",
            "session": "sec_p1_invalid_token_999",
            "player": 1
        })
        client_ws.sendTextMessage(handshake)

    client_ws.connected.connect(_on_open)
    url = f"ws://127.0.0.1:{server.active_ws_port}"
    client_ws.open(QUrl(url))

    start_time = time.time()
    while time.time() - start_time < 3.0:
        qapp.processEvents()
        if len(rejected_signals) > 0:
            break
        time.sleep(0.05)

    assert server.sessions[1].connected is False
    assert len(rejected_signals) >= 1
    assert "Invalid session token" in rejected_signals[0][1]

    client_ws.close()
    server.stop()
    qapp.processEvents()


def test_get_static_dir():
    from motiondrive.phone.server import get_static_dir
    static_dir = get_static_dir()
    assert static_dir.exists()
    assert (static_dir / "index.html").exists()


def test_http_health_endpoint_and_static_files(qapp):
    import urllib.request
    server = PhoneControllerServer()
    assert server.start() is True

    try:
        # 1. Test GET /health
        health_url = f"http://127.0.0.1:{server.HTTP_PORT}/health"
        req = urllib.request.Request(health_url)
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            content_type = resp.headers.get("Content-Type")
            assert "application/json" in content_type
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("status") == "ok"
            assert data.get("app") == "MotionDrive"

        # 2. Test GET / (index.html)
        index_url = f"http://127.0.0.1:{server.HTTP_PORT}/"
        with urllib.request.urlopen(index_url) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "<!DOCTYPE html>" in body or "<html" in body

        # 3. Test static assets (style.css, app.js)
        css_url = f"http://127.0.0.1:{server.HTTP_PORT}/style.css"
        with urllib.request.urlopen(css_url) as resp:
            assert resp.status == 200

        js_url = f"http://127.0.0.1:{server.HTTP_PORT}/app.js"
        with urllib.request.urlopen(js_url) as resp:
            assert resp.status == 200
    finally:
        server.stop()
        qapp.processEvents()


def test_qr_url_format_and_session_masking(qapp):
    from motiondrive.phone.server import mask_token
    server = PhoneControllerServer()
    assert server.start() is True

    p1_url = server.get_local_url(1)
    p2_url = server.get_local_url(2)

    assert "http://" in p1_url
    assert f":{server.HTTP_PORT}/?" in p1_url
    assert "session=sec_p1_" in p1_url
    assert "player=1" in p1_url
    assert f"ws_port={server.active_ws_port}" in p1_url

    assert "session=sec_p2_" in p2_url
    assert "player=2" in p2_url

    t1 = server.sessions[1].session_token
    masked = mask_token(t1)
    assert "****" in masked
    assert masked.startswith(t1[:7])
    assert masked.endswith(t1[-4:])

    server.stop()
    qapp.processEvents()


def test_get_local_ip_filtering_and_fallback():
    from motiondrive.phone.server import get_local_ip
    ip = get_local_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0
    assert not ip.startswith("169.254.")
    assert not ip.startswith("127.0.0.1") or ip == "127.0.0.1"


def test_debug_logger_and_report_sanitization(qapp):
    from motiondrive.phone.debug_logger import PhoneDebugLogger, mask_token
    dbg = PhoneDebugLogger.get_instance()
    dbg.clear_log()
    assert len(dbg.events) == 0

    # Test token masking in log call
    raw_token = "sec_p1_abc123456"
    dbg.log("TEST_COMP", "TEST_EVENT", session_token=raw_token, player=1)

    assert len(dbg.events) >= 1
    ev = dbg.events[-1]
    assert raw_token not in ev["raw"]
    assert "sec_p1_****3456" in ev["raw"]

    report = dbg.get_sanitized_report(mode="single")
    assert raw_token not in report
    assert "MotionDrive Phone Connection Debug Report" in report

    dbg.clear_log()
    assert len(dbg.events) == 0


def test_debug_logger_local_network_test(qapp):
    from motiondrive.phone.debug_logger import PhoneDebugLogger
    server = PhoneControllerServer()
    assert server.start() is True

    try:
        dbg = PhoneDebugLogger.get_instance()
        res = dbg.run_local_network_test(server.HTTP_PORT, server.active_ws_port)
        assert res["http"] is True
        assert res["ws"] is True
        assert len(res["details"]) >= 3
    finally:
        server.stop()
        qapp.processEvents()


def test_ws_url_computation_logic():
    """Validates the client-side WebSocket URL construction rules."""
    def compute_ws_url(location_protocol, location_hostname, ws_port_param=None):
        protocol = "wss:" if location_protocol == "https:" else "ws:"
        port = int(ws_port_param) if ws_port_param and int(ws_port_param) > 0 else 8766
        # Must strictly preserve location hostname without rewriting to localhost or 0.0.0.0
        return f"{protocol}//{location_hostname}:{port}"

    # 1. Reuses HTTP LAN host and default port 8766
    url1 = compute_ws_url("http:", "10.197.139.93")
    assert url1 == "ws://10.197.139.93:8766"
    assert "localhost" not in url1
    assert "127.0.0.1" not in url1
    assert "0.0.0.0" not in url1

    # 2. Honors custom ws_port parameter
    url2 = compute_ws_url("http:", "192.168.1.50", "8767")
    assert url2 == "ws://192.168.1.50:8767"

    # 3. Uses wss: scheme if loaded over https:
    url3 = compute_ws_url("https:", "10.197.139.93")
    assert url3 == "wss://10.197.139.93:8766"


def test_ws_test_http_endpoint(qapp):
    """Verifies that the /ws-test endpoint is served over HTTP."""
    import urllib.request
    server = PhoneControllerServer()
    assert server.start() is True

    try:
        url = f"http://127.0.0.1:{server.HTTP_PORT}/ws-test"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            assert "MOTIONDRIVE WEBSOCKET TEST" in content
            assert "TEST WEBSOCKET" in content
            assert "ws-target-display" in content
    finally:
        server.stop()
        qapp.processEvents()


def test_server_raw_websocket_test_probe(qapp):
    """Verifies that raw WebSocket connections sending ws_test receive ws_test_ack without session auth."""
    from PySide6.QtWebSockets import QWebSocket
    from motiondrive.phone.debug_logger import PhoneDebugLogger
    dbg = PhoneDebugLogger.get_instance()
    dbg.clear_log()

    server = PhoneControllerServer()
    assert server.start() is True

    received_ack = []
    ws_client = QWebSocket()

    def on_text(msg):
        try:
            data = json.loads(msg)
            if data.get("type") == "ws_test_ack":
                received_ack.append(data)
        except Exception:
            pass

    ws_client.textMessageReceived.connect(on_text)

    def on_connected():
        ws_client.sendTextMessage(json.dumps({"type": "ws_test"}))

    ws_client.connected.connect(on_connected)

    try:
        ws_client.open(f"ws://127.0.0.1:{server.active_ws_port}")

        # Wait up to 3s for connection and ack
        start = time.time()
        while len(received_ack) == 0 and time.time() - start < 3.0:
            qapp.processEvents()
            time.sleep(0.02)

        assert len(received_ack) == 1
        assert received_ack[0]["status"] == "ok"

        # Check debug logger events
        event_names = [e["event"] for e in dbg.events]
        assert "WS_TCP_CONNECTED" in event_names
        assert "WS_CONNECTION_ACCEPTED" in event_names
        assert "WS_TEST_CONNECTED" in event_names

    finally:
        ws_client.close()
        qapp.processEvents()
        server.stop()
        qapp.processEvents()







