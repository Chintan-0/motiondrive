"""Local Wi-Fi Phone Controller Server for MotionDrive Desktop.

Hosts a lightweight HTTP static server on port 8765 and a PySide6 QWebSocketServer
on port 8766. Handles local session pairing, single-phone policy, heartbeat
watchdog, and emits normalized control signals.
"""
from __future__ import annotations

import http.server
import json
import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer, Qt, QByteArray
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocketServer, QWebSocket

from motiondrive.logging_ import get_logger
from motiondrive.paths import resource_path
from motiondrive.phone.qrcode_gen import generate_qr_svg

log = get_logger(__name__)

try:
    from zeroconf import Zeroconf, ServiceInfo
    _ZEROCONF_AVAILABLE = True
except ImportError:
    _ZEROCONF_AVAILABLE = False


def mask_token(token: str) -> str:
    """Masks secret session token for safe logging output (e.g. sec_p1_****5413)."""
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "****"
    return f"{token[:7]}****{token[-4:]}"


def get_local_ip() -> str:
    """Finds active local IPv4 address on Wi-Fi, Hotspot, or LAN interface.

    Filters out loopback (127.0.0.1), link-local (169.254.x.x), and virtual
    adapters (vEthernet, WSL, Docker, VMware, VirtualBox, Hyper-V, VPN, etc).
    """
    candidates = []
    wifi_lan_candidates = []

    virtual_keywords = [
        "vethernet", "wsl", "docker", "vmware", "virtualbox", "loopback",
        "bluetooth", "hyper-v", "default switch", "virtual", "vpn",
        "wireguard", "tailscale", "zerotier", "hamachi", "tap", "tun",
        "npcap", "pcap", "pseudo"
    ]

    # 1. Multi-target UDP routing probe (probes external WAN + local gateway subnets)
    probe_ip = None
    probe_targets = [
        ("8.8.8.8", 80),         # Google DNS (Standard WAN)
        ("1.1.1.1", 80),         # Cloudflare DNS (Standard WAN)
        ("192.168.43.1", 80),     # Android Hotspot default gateway
        ("172.20.10.1", 80),      # iOS Hotspot default gateway
        ("192.168.1.1", 80),      # Standard Wi-Fi router gateway
        ("192.168.0.1", 80),      # Standard Wi-Fi router gateway
        ("10.0.0.1", 80),         # Enterprise LAN router gateway
    ]

    for target in probe_targets:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.2)
            s.connect(target)
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                probe_ip = ip
                break
        except Exception:
            pass

    # 2. Inspect active network interfaces via psutil
    try:
        import psutil
        stats = psutil.net_if_stats() if hasattr(psutil, "net_if_stats") else {}
        for iface, addrs in psutil.net_if_addrs().items():
            iface_lower = iface.lower()
            if any(bad in iface_lower for bad in virtual_keywords):
                continue
            if iface in stats and not stats[iface].isup:
                continue
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    if ip.startswith("127.") or ip.startswith("169.254."):
                        continue
                    candidates.append((iface, ip))
                    if any(w in iface_lower for w in ["wi-fi", "wifi", "wireless", "wlan", "hotspot", "ethernet", "lan"]):
                        wifi_lan_candidates.append((iface, ip))
    except Exception:
        pass

    log.info("DISCOVERY: Network IP candidates=%s (probe_ip=%s)", candidates, probe_ip)

    if probe_ip and any(ip == probe_ip for _, ip in candidates):
        log.info("DISCOVERY: Selected probe IPv4 %s", probe_ip)
        return probe_ip
    if wifi_lan_candidates:
        selected = wifi_lan_candidates[0][1]
        log.info("DISCOVERY: Selected wifi/lan IPv4 %s (%s)", selected, wifi_lan_candidates[0][0])
        return selected
    if candidates:
        selected = candidates[0][1]
        log.info("DISCOVERY: Selected candidate IPv4 %s (%s)", selected, candidates[0][0])
        return selected

    final_ip = probe_ip or "127.0.0.1"
    log.info("DISCOVERY: Fallback IPv4 %s", final_ip)
    return final_ip


def get_static_dir() -> Path:
    """Resolves the directory containing mobile controller static files."""
    static_dir = resource_path("src/motiondrive/phone/static")
    if static_dir.exists() and (static_dir / "index.html").exists():
        return static_dir
    fallback = resource_path("phone_static")
    if fallback.exists() and (fallback / "index.html").exists():
        return fallback
    return static_dir


class _StaticHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Serves mobile controller static files and health endpoint."""

    def __init__(self, *args, directory=None, **kwargs):
        static_dir = str(get_static_dir())
        super().__init__(*args, directory=static_dir, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def guess_type(self, path):
        path_str = str(path).lower()
        if path_str.endswith(".js"):
            return "application/javascript"
        if path_str.endswith(".css"):
            return "text/css"
        if path_str.endswith(".html"):
            return "text/html"
        return super().guess_type(path)

    def do_GET(self):
        client_ip = self.client_address[0] if hasattr(self, "client_address") else "unknown"
        log.info("PHONE_HTTP_REQUEST client_ip=%s path=%s", client_ip, self.path)
        clean_path = self.path.split("?")[0].rstrip("/")

        if clean_path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            body = json.dumps({"status": "ok", "app": "MotionDrive"}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            log.info("PHONE_HTTP_HEALTH_OK client_ip=%s", client_ip)
            return

        if clean_path in ("/ws-test", "/ws-test.html"):
            ws_test_path = get_static_dir() / "ws_test.html"
            if ws_test_path.exists():
                content = ws_test_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                log.info("PHONE_HTTP_WS_TEST_SERVED client_ip=%s", client_ip)
                return

        if clean_path == "/client-log":
            try:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                evt_name = qs.get("event", ["UNKNOWN"])[0]
                player_id = int(qs.get("player", ["1"])[0])
                details = qs.get("details", [""])[0]
                extra = {}
                for k, v in qs.items():
                    if k not in ("event", "player", "details"):
                        extra[k] = v[0] if v else ""
                if details:
                    extra["details"] = details

                from motiondrive.phone.debug_logger import PhoneDebugLogger
                dbg = PhoneDebugLogger.get_instance()
                dbg.log("PHONE_CLIENT", evt_name, player=player_id, client_ip=client_ip, **extra)
                log.info("PHONE_CLIENT_EVENT player=%d event=%s client_ip=%s extra=%s", player_id, evt_name, client_ip, extra)
            except Exception as e:
                log.warning("Failed to process client log event: %s", e)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            body = b'{"status":"ok"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Track HTTP reachability for player session
        if clean_path in ("", "/", "/index.html"):
            try:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                player_val = int(qs.get("player", ["1"])[0])
                from motiondrive.phone.debug_logger import PhoneDebugLogger
                dbg = PhoneDebugLogger.get_instance()
                dbg.update_pipeline_stage(player_val, "http", True, client_ip)
                dbg.log("PHONE_SERVER", "HTTP_REQUEST_RECEIVED", player=player_val, client_ip=client_ip, path=clean_path)
            except Exception:
                pass

        super().do_GET()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_POST(self):
        client_ip = self.client_address[0] if hasattr(self, "client_address") else "unknown"
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path == "/client-log":
            try:
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len > 0 else b"{}"
                data = json.loads(body.decode("utf-8"))
                evt_name = data.get("event", "UNKNOWN")
                player_id = int(data.get("player", 1))
                extra = {k: str(v) for k, v in data.items() if k not in ("event", "player")}
                from motiondrive.phone.debug_logger import PhoneDebugLogger
                dbg = PhoneDebugLogger.get_instance()
                dbg.log("PHONE_CLIENT", evt_name, player=player_id, client_ip=client_ip, **extra)
                log.info("PHONE_CLIENT_EVENT player=%d event=%s client_ip=%s extra=%s", player_id, evt_name, client_ip, extra)
            except Exception as e:
                log.warning("Failed to process POST client log: %s", e)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            body = b'{"status":"ok"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        super().do_GET()

    def log_message(self, format, *args):
        pass  # Suppress noisy HTTP access logs


@dataclass
class PlayerSession:
    player_id: int
    session_token: str
    socket: QWebSocket | None = None
    connected: bool = False
    last_packet_time: float = 0.0
    client_ip: str = ""
    steering: float = 0.0
    throttle: float = 0.0
    brake: float = 0.0
    shift: bool = False


def _get_socket_conn_id(socket: Any) -> str:
    try:
        if hasattr(socket, "property") and callable(getattr(socket, "property")):
            cid = socket.property("connection_id")
            if cid:
                return str(cid)
    except Exception:
        pass
    return "WS-UNKNOWN"


class PhoneControllerServer(QObject):
    """Local network server for Phone Controller pairing & WebSocket streaming supporting up to 2 players."""

    # Signals: (player_id, steering, throttle, brake, shift, timestamp)
    input_received = Signal(int, float, float, float, bool, float)
    phone_connected = Signal(int, str)                     # (player_id, client_ip)
    phone_disconnected = Signal(int)                       # (player_id)
    connection_lost = Signal(int)                        # (player_id) watchdog timeout trigger
    session_rejected = Signal(str, str)                    # (client_ip, reason)

    HTTP_PORT = 8765
    WS_PORT = 8766
    WATCHDOG_TIMEOUT_SEC = 1.25                          # Neutralize if no packet for 1.25s

    def __init__(self, parent=None):
        super().__init__(parent)
        self.local_ip = get_local_ip()
        self.running = False
        self.active_ws_port = self.WS_PORT

        self.sessions: dict[int, PlayerSession] = {}
        self.urls: dict[int, str] = {}
        self._socket_map: dict[QWebSocket, int] = {}

        self._http_server: http.server.ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._ws_server: QWebSocketServer | None = None

        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setInterval(250)
        self._watchdog_timer.timeout.connect(self._check_watchdog)

    @property
    def session_token(self) -> str:
        return self.sessions[1].session_token if 1 in self.sessions else ""

    @property
    def local_url(self) -> str:
        return self.get_local_url(1)

    def get_local_url(self, player_id: int = 1) -> str:
        return self.urls.get(player_id, "")

    def start(self) -> bool:
        """Starts HTTP server & WebSocket server with new random session tokens for Player 1 and Player 2."""
        if self.running:
            return True

        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()

        self.local_ip = get_local_ip()
        dbg.pipeline_state["selected_ip"] = self.local_ip

        dbg.log("PHONE_SERVER", "PHONE_SERVER_START", http_bind=f"0.0.0.0:{self.HTTP_PORT}", ws_bind=f"0.0.0.0:{self.WS_PORT}", local_ip=self.local_ip)

        # Initialize slots for Player 1 and Player 2
        for pid in (1, 2):
            token = f"sec_p{pid}_{uuid.uuid4().hex[:8]}"
            self.sessions[pid] = PlayerSession(player_id=pid, session_token=token)

        # 1. Start HTTP Static File Server listening on ALL interfaces (0.0.0.0)
        try:
            log.info("PHONE HTTP SERVER STARTING host=0.0.0.0 port=%d", self.HTTP_PORT)
            dbg.log("PHONE_SERVER", "HTTP_SERVER_START", bind="0.0.0.0", port=self.HTTP_PORT)
            self._http_server = http.server.ThreadingHTTPServer(
                ("0.0.0.0", self.HTTP_PORT), _StaticHTTPHandler
            )
            self._http_thread = threading.Thread(
                target=self._http_server.serve_forever, daemon=True
            )
            self._http_thread.start()
            dbg.pipeline_state["http_ready"] = True
            dbg.pipeline_state["http_bind"] = f"0.0.0.0:{self.HTTP_PORT}"
            log.info("PHONE HTTP SERVER READY bound_port=%d", self.HTTP_PORT)
            dbg.log("PHONE_SERVER", "HTTP_SERVER_READY", listen=f"0.0.0.0:{self.HTTP_PORT}")
            log.info("Phone HTTP Server listening on 0.0.0.0:%d (reachable at http://%s:%d)", self.HTTP_PORT, self.local_ip, self.HTTP_PORT)
        except Exception as e:
            log.exception("Failed to start Phone HTTP Server on port %d: %s", self.HTTP_PORT, e)
            dbg.log("PHONE_SERVER", "HTTP_SERVER_ERROR", bind="0.0.0.0", port=self.HTTP_PORT, error=str(e), level="ERROR")
            return False

        # 2. Start PySide6 QWebSocketServer bound explicitly to QHostAddress.AnyIPv4 (0.0.0.0)
        ws_bound = False
        dbg.log("PHONE_SERVER", "WS_SERVER_START", bind="0.0.0.0", port=self.WS_PORT)
        for port in range(self.WS_PORT, self.WS_PORT + 10):
            try:
                self._ws_server = QWebSocketServer(
                    "MotionDrivePhoneServer", QWebSocketServer.NonSecureMode, self
                )
                if self._ws_server.listen(QHostAddress.AnyIPv4, port):
                    self.active_ws_port = port
                    self._ws_server.newConnection.connect(self._on_new_connection)
                    dbg.pipeline_state["ws_ready"] = True
                    dbg.pipeline_state["ws_bind"] = f"0.0.0.0:{port}"
                    log.info("Phone WebSocket Server listening on 0.0.0.0:%d (reachable at ws://%s:%d)", port, self.local_ip, port)
                    dbg.log("PHONE_SERVER", "WS_SERVER_READY", listen=f"0.0.0.0:{port}")
                    ws_bound = True
                    break
                else:
                    err_str = self._ws_server.errorString()
                    log.warning("QWebSocketServer listen failed on port %d: %s", port, err_str)
                    self._ws_server.close()
                    self._ws_server = None
            except Exception as e:
                log.warning("QWebSocketServer bind exception on port %d: %s", port, e)
                if self._ws_server is not None:
                    self._ws_server.close()
                    self._ws_server = None

        if not ws_bound:
            log.error("Failed to bind Phone WebSocket Server to any port in range %d-%d", self.WS_PORT, self.WS_PORT + 10)
            dbg.log("PHONE_SERVER", "WS_SERVER_ERROR", bind="0.0.0.0", range=f"{self.WS_PORT}-{self.WS_PORT+10}", level="ERROR")
            return False

        for pid in (1, 2):
            self.urls[pid] = f"http://{self.local_ip}:{self.HTTP_PORT}/?session={self.sessions[pid].session_token}&player={pid}&ws_port={self.active_ws_port}"
            dbg.log(
                "QR_GENERATED",
                "QR_GENERATED",
                player=pid,
                http_host=self.local_ip,
                http_port=self.HTTP_PORT,
                ws_host=self.local_ip,
                ws_port=self.active_ws_port,
                session_prefix=self.sessions[pid].session_token[:7],
            )

        log.info("PHONE URL (P1): http://%s:%d/?session=%s&player=1&ws_port=%d", self.local_ip, self.HTTP_PORT, mask_token(self.sessions[1].session_token), self.active_ws_port)
        log.info("PHONE URL (P2): http://%s:%d/?session=%s&player=2&ws_port=%d", self.local_ip, self.HTTP_PORT, mask_token(self.sessions[2].session_token), self.active_ws_port)
        log.info("Phone Controller Server ready (P1: %s, P2: %s)", self.urls[1], self.urls[2])
        self._start_mdns()
        self.running = True
        self._watchdog_timer.start()
        return True

    def stop(self) -> None:
        """Stops all servers, closes active sockets, and neutralizes control state for all players."""
        self.running = False
        self._watchdog_timer.stop()
        self._stop_mdns()

        for pid, session in self.sessions.items():
            if session.socket is not None:
                try:
                    session.socket.close()
                except Exception:
                    pass
                session.socket = None
                session.connected = False

        self._socket_map.clear()

        if self._ws_server is not None:
            try:
                self._ws_server.close()
            except Exception:
                pass
            self._ws_server = None

        if self._http_server is not None:
            try:
                self._http_server.shutdown()
                self._http_server.server_close()
            except Exception:
                pass
            self._http_server = None

        self._neutralize_all("Server stopped")
        log.info("Phone Controller Server stopped")

    def _start_mdns(self) -> None:
        if not _ZEROCONF_AVAILABLE or not self.local_ip:
            return
        try:
            self._zeroconf = Zeroconf()
            self._mdns_info = ServiceInfo(
                "_http._tcp.local.",
                "MotionDrive Phone Controller._http._tcp.local.",
                addresses=[socket.inet_aton(self.local_ip)],
                port=self.HTTP_PORT,
                properties={"path": "/"},
                server="motiondrive.local.",
            )
            self._zeroconf.register_service(self._mdns_info)
            log.info("mDNS registered: motiondrive.local.")
        except Exception as e:
            log.warning("mDNS registration failed (non-fatal): %s", e)
            self._zeroconf = None
            self._mdns_info = None

    def _stop_mdns(self) -> None:
        if getattr(self, "_zeroconf", None) is not None:
            try:
                if getattr(self, "_mdns_info", None) is not None:
                    self._zeroconf.unregister_service(self._mdns_info)
                self._zeroconf.close()
            except Exception:
                pass
            self._zeroconf = None
            self._mdns_info = None

    def get_qr_svg(self, player_id: int = 1) -> str:
        """Generates QR Code SVG string for the specified player connection URL."""
        return generate_qr_svg(self.get_local_url(player_id))

    def _on_new_connection(self) -> None:
        if self._ws_server is None:
            return
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()

        socket = self._ws_server.nextPendingConnection()
        client_ip = socket.peerAddress().toString()
        port = socket.peerPort()
        conn_id = dbg.next_connection_id()
        if hasattr(socket, "setProperty") and callable(getattr(socket, "setProperty")):
            try:
                socket.setProperty("connection_id", conn_id)
            except Exception:
                pass

        socket.textMessageReceived.connect(lambda msg, s=socket: self._on_message_received(msg, s))
        socket.binaryMessageReceived.connect(lambda data, s=socket: self._on_binary_message_received(data, s))
        socket.disconnected.connect(lambda s=socket: self._on_socket_disconnected(s))
        log.info("WS_TCP_CONNECTED client_ip=%s port=%d connection_id=%s", client_ip, port, conn_id)
        dbg.log("PHONE_SERVER", "WS_TCP_CONNECTED", connection_id=conn_id, client_ip=client_ip, client_port=port)
        log.info("WS_CONNECTION_ACCEPTED connection_id=%s client_ip=%s", conn_id, client_ip)
        dbg.log("PHONE_SERVER", "WS_CONNECTION_ACCEPTED", connection_id=conn_id, client_ip=client_ip)

    def _authenticate_socket(self, socket: QWebSocket, token: str) -> int | None:
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        conn_id = _get_socket_conn_id(socket)

        client_ip = socket.peerAddress().toString()
        masked = mask_token(token)

        dbg.log("PHONE_SERVER", "SESSION_AUTH_START", connection_id=conn_id, player_query=token[:7] if token else "none", client_ip=client_ip)

        target_pid = None
        for pid, session in self.sessions.items():
            if session.session_token == token:
                target_pid = pid
                break

        log.info("SESSION RECEIVED player_from_query=%s session_valid=%s assigned_player=%s session=%s client=%s",
                 target_pid if target_pid else "none",
                 target_pid is not None,
                 target_pid if target_pid else "none",
                 masked, client_ip)

        if target_pid is None:
            log.warning("SESSION REJECTED reason=Invalid session token session=%s client=%s", masked, client_ip)
            dbg.log("PHONE_SERVER", "SESSION_AUTH_FAILED", connection_id=conn_id, reason="invalid_token", client_ip=client_ip, level="WARN")
            self.session_rejected.emit(client_ip, "Invalid session token")
            reject_msg = json.dumps({
                "version": 1,
                "type": "rejected",
                "message": "Invalid session token."
            })
            try:
                socket.sendTextMessage(reject_msg)
                socket.close()
            except Exception:
                pass
            return None

        session = self.sessions[target_pid]
        if session.connected and session.socket is not None and session.socket != socket:
            log.warning("SESSION REJECTED reason=Slot occupied player=%d session=%s client=%s", target_pid, masked, client_ip)
            dbg.log("PHONE_SERVER", "SESSION_AUTH_FAILED", connection_id=conn_id, player=target_pid, reason="player_slot_unavailable", client_ip=client_ip, level="WARN")
            self.session_rejected.emit(client_ip, f"Player {target_pid} slot occupied")
            reject_msg = json.dumps({
                "version": 1,
                "type": "rejected",
                "message": f"Player {target_pid} slot is occupied."
            })
            try:
                socket.sendTextMessage(reject_msg)
                socket.close()
            except Exception:
                pass
            return None

        log.info("SESSION VALID player=%d session=%s client=%s", target_pid, masked, client_ip)
        dbg.log("PHONE_SERVER", "SESSION_AUTH_SUCCESS", connection_id=conn_id, player=target_pid, client_ip=client_ip)
        session.socket = socket
        session.connected = True
        session.client_ip = client_ip
        session.steering = 0.0
        session.throttle = 0.0
        session.brake = 0.0
        session.shift = False
        session.last_packet_time = time.time()
        self._socket_map[socket] = target_pid
        self._neutralize_player(target_pid, "Socket authenticated reset")
        log.info("PLAYER SESSION CONNECTED player=%d client=%s", target_pid, client_ip)
        log.info("PHONE_CONNECTED_SIGNAL player=%d client=%s", target_pid, client_ip)
        dbg.log("PHONE_SERVER", "PLAYER_SESSION_CONNECTED", player=target_pid, connection_id=conn_id, client_ip=client_ip)
        dbg.log("PHONE_SERVER", "PHONE_CONNECTED_SIGNAL", player=target_pid, connection_id=conn_id, client_ip=client_ip)
        dbg.update_pipeline_stage(target_pid, "session", True, client_ip)
        dbg.update_pipeline_stage(target_pid, "ws", True, client_ip)
        self.phone_connected.emit(target_pid, client_ip)
        return target_pid

    def _on_socket_disconnected(self, socket: QWebSocket) -> None:
        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        conn_id = _get_socket_conn_id(socket)

        is_test = False
        if hasattr(socket, "property") and callable(getattr(socket, "property")):
            try:
                is_test = bool(socket.property("is_ws_test"))
            except Exception:
                pass

        if is_test:
            client_ip = socket.peerAddress().toString() if hasattr(socket, "peerAddress") else "unknown"
            code = socket.closeCode() if hasattr(socket, "closeCode") else 0
            log.info("WS_TEST_DISCONNECTED client_ip=%s connection_id=%s code=%s", client_ip, conn_id, code)
            dbg.log("PHONE_SERVER", "WS_TEST_DISCONNECTED", connection_id=conn_id, client_ip=client_ip, code=code)
            return

        pid = self._socket_map.pop(socket, None)
        if pid is not None:
            session = self.sessions.get(pid)
            if session and session.socket == socket:
                log.info("Player %d disconnected", pid)
                dbg.log("PHONE_SERVER", "WS_DISCONNECTED", connection_id=conn_id, player=pid, client_ip=session.client_ip, reason="client_closed")
                dbg.log("PHONE_SERVER", "PLAYER_SESSION_DISCONNECTED", player=pid, connection_id=conn_id, reason="socket_disconnected")
                dbg.update_pipeline_stage(pid, "ws", False)
                dbg.update_pipeline_stage(pid, "session", False)
                dbg.update_pipeline_stage(pid, "handshake", False)
                dbg.update_pipeline_stage(pid, "ui", False)
                session.socket = None
                session.connected = False
                self._neutralize_player(pid, "Phone disconnected")
                self.phone_disconnected.emit(pid)

    def _on_binary_message_received(self, data: bytes | QByteArray, socket: QWebSocket) -> None:
        pid = self._socket_map.get(socket)
        if pid is None:
            return  # Pending authentication via handshake/token

        session = self.sessions[pid]
        if isinstance(data, QByteArray):
            data = bytes(data)
        if len(data) < 6:
            return
        try:
            steering_raw, throttle_raw, brake_raw, flags, seq = struct.unpack("<hBBBB", data[:6])
        except Exception:
            log.exception("Malformed 6-byte binary packet received")
            return

        steering = max(-1.0, min(1.0, steering_raw / 32767.0))
        throttle = max(0.0, min(1.0, throttle_raw / 255.0))
        brake = max(0.0, min(1.0, brake_raw / 255.0))
        shift = bool(flags & 0x01)
        emergency_stop = bool(flags & 0x04)

        ts = time.time()
        session.last_packet_time = ts

        from motiondrive.phone.debug_logger import PhoneDebugLogger
        PhoneDebugLogger.get_instance().log_control_packet(pid, steering, throttle, brake, flags)

        if emergency_stop:
            log.warning("Received Emergency Stop signal from Player %d phone binary packet", pid)
            PhoneDebugLogger.get_instance().log("PHONE_SERVER", "EMERGENCY_STOP_RECEIVED", player=pid, level="WARN")
            self._neutralize_all("Phone Emergency Stop")
            parent_obj = self.parent()
            if parent_obj is not None and hasattr(parent_obj, "emergency_stop"):
                parent_obj.emergency_stop()
            return

        session.steering = steering
        session.throttle = throttle
        session.brake = brake
        session.shift = shift

        self.input_received.emit(pid, steering, throttle, brake, shift, ts)

        if hasattr(socket, "state"):
            try:
                from PySide6.QtNetwork import QAbstractSocket
                if socket.state() != QAbstractSocket.SocketState.ConnectedState:
                    return
            except Exception:
                pass

    def _on_message_received(self, message: str, socket: QWebSocket) -> None:
        try:
            data = json.loads(message)
        except Exception:
            return

        if not isinstance(data, dict):
            return

        from motiondrive.phone.debug_logger import PhoneDebugLogger
        dbg = PhoneDebugLogger.get_instance()
        conn_id = _get_socket_conn_id(socket)
        msg_type = data.get("type", "")

        # Handle diagnostic WebSocket test endpoint (/ws-test)
        if msg_type == "ws_test":
            if hasattr(socket, "setProperty") and callable(getattr(socket, "setProperty")):
                socket.setProperty("is_ws_test", True)
            client_ip = socket.peerAddress().toString() if hasattr(socket, "peerAddress") else "unknown"
            log.info("WS_TEST_CONNECTED client_ip=%s connection_id=%s", client_ip, conn_id)
            dbg.log("PHONE_SERVER", "WS_TEST_CONNECTED", connection_id=conn_id, client_ip=client_ip)
            try:
                socket.sendTextMessage(json.dumps({
                    "version": 1,
                    "type": "ws_test_ack",
                    "status": "ok",
                    "timestamp": time.time()
                }))
            except Exception:
                pass
            return

        if data.get("version") != 1:
            return

        token = data.get("session", "")
        pid = self._socket_map.get(socket)

        if pid is None:
            pid = self._authenticate_socket(socket, token)
            if pid is None:
                return

        session = self.sessions[pid]
        if msg_type == "handshake":
            log.info("WS_HANDSHAKE_RECEIVED player=%d session=%s client_ip=%s", pid, mask_token(token), session.client_ip)
            dbg.log("PHONE_SERVER", "WS_HANDSHAKE_RECEIVED", connection_id=conn_id, player=pid, session_present=bool(token), client_ip=session.client_ip)
            session.last_packet_time = time.time()
            ack_msg = json.dumps({
                "version": 1,
                "type": "handshake_ack",
                "player": pid,
                "authenticated": True
            })
            try:
                socket.sendTextMessage(ack_msg)
                log.info("WS_HANDSHAKE_ACK_SENT player=%d client_ip=%s", pid, session.client_ip)
                dbg.log("PHONE_SERVER", "WS_HANDSHAKE_ACK_SENT", connection_id=conn_id, player=pid, client_ip=session.client_ip)
                dbg.update_pipeline_stage(pid, "handshake", True, session.client_ip)
            except Exception:
                pass
            return

        if msg_type == "input":
            steering = float(data.get("steering", 0.0))
            throttle = float(data.get("throttle", 0.0))
            brake = float(data.get("brake", 0.0))
            shift = bool(data.get("shift", False))

            steering = max(-1.0, min(1.0, steering))
            throttle = max(0.0, min(1.0, throttle))
            brake = max(0.0, min(1.0, brake))

            ts = float(data.get("timestamp", time.time()))
            session.last_packet_time = time.time()

            session.steering = steering
            session.throttle = throttle
            session.brake = brake
            session.shift = shift

            self.input_received.emit(pid, steering, throttle, brake, shift, ts)

            try:
                socket.sendTextMessage(json.dumps({"type": "ack", "ts": ts}))
            except Exception:
                pass

    def _check_watchdog(self) -> None:
        if not self.running:
            return
        now = time.time()
        for pid, session in list(self.sessions.items()):
            if session.connected and session.last_packet_time > 0 and (now - session.last_packet_time) > self.WATCHDOG_TIMEOUT_SEC:
                log.warning("Player %d connection watchdog timeout -- neutralizing input", pid)
                self.connection_lost.emit(pid)
                self._neutralize_player(pid, "Watchdog timeout")

    def _neutralize_player(self, player_id: int, reason: str) -> None:
        session = self.sessions.get(player_id)
        if session:
            session.steering = 0.0
            session.throttle = 0.0
            session.brake = 0.0
            session.shift = False
            session.last_packet_time = 0.0
            self.input_received.emit(player_id, 0.0, 0.0, 0.0, False, time.time())

    def _neutralize_all(self, reason: str) -> None:
        for pid in list(self.sessions):
            self._neutralize_player(pid, reason)
