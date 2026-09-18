"""Dedicated Phone Connection Debug Logger & Observability System for MotionDrive."""
from __future__ import annotations

import datetime
import json
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path
from typing import Dict, Any, List

from motiondrive.paths import LOGS_DIR

_LOG_DIR = LOGS_DIR
_LOG_FILE = _LOG_DIR / "phone_connection_debug.log"


def mask_token(token: str) -> str:
    """Masks secret session token for safe logging output (e.g. sec_p1_****5413)."""
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "****"
    return f"{token[:7]}****{token[-4:]}"


class PhoneDebugLogger:
    """Thread-safe, exception-safe logger for phone controller connection diagnostics."""

    _instance: PhoneDebugLogger | None = None
    _lock = threading.Lock()

    def __init__(self):
        self._file_lock = threading.Lock()
        self._events_lock = threading.Lock()
        self.events: List[Dict[str, Any]] = []
        self._conn_counter = 0

        # Control packet rate limiting per player (1 log per sec)
        self._last_control_log: Dict[int, float] = {}
        self._control_counts: Dict[int, int] = {}
        self._last_control_state: Dict[int, Dict[str, float]] = {}

        # Pipeline stage state tracking
        self.pipeline_state: Dict[str, Any] = {
            "http_ready": False,
            "ws_ready": False,
            "http_bind": "",
            "ws_bind": "",
            "selected_iface": "Unknown",
            "selected_ip": "127.0.0.1",
            "p1": {"http": False, "ws": False, "handshake": False, "session": False, "ui": False, "ip": ""},
            "p2": {"http": False, "ws": False, "handshake": False, "session": False, "ui": False, "ip": ""},
        }

        self._init_log_file()

    @classmethod
    def get_instance(cls) -> PhoneDebugLogger:
        with cls._lock:
            if cls._instance is None:
                cls._instance = PhoneDebugLogger()
            return cls._instance

    def _init_log_file(self) -> None:
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n============================================================\n")
                f.write(f"=== MotionDrive Phone Connection Debug Log Started: {ts} ===\n")
                f.write(f"============================================================\n")
        except Exception:
            pass

    def next_connection_id(self) -> str:
        with self._file_lock:
            self._conn_counter += 1
            return f"WS-{self._conn_counter:04d}"

    def log(self, component: str, event: str, level: str = "INFO", **kwargs) -> None:
        """Logs structured key-value event to file and in-memory buffer."""
        now = datetime.datetime.now()
        ts_str = now.strftime("%H:%M:%S") + f".{now.microsecond // 1000:03d}"

        # Sanitize token in kwargs if present
        sanitized_kwargs = {}
        for k, v in kwargs.items():
            if "token" in k.lower() or k.lower() in ("session", "token", "session_token"):
                sanitized_kwargs[k] = mask_token(str(v))
            else:
                sanitized_kwargs[k] = str(v)

        kv_pairs = " ".join(f"{k}={v}" for k, v in sanitized_kwargs.items())
        line = f"[{ts_str}] [{level}] [{component}] {event} {kv_pairs}".strip()

        event_obj = {
            "timestamp": ts_str,
            "level": level,
            "component": component,
            "event": event,
            "kv": sanitized_kwargs,
            "raw": line,
        }

        # Update in-memory log buffer
        with self._events_lock:
            self.events.append(event_obj)
            if len(self.events) > 500:
                self.events.pop(0)

        # Write to log file
        try:
            with self._file_lock:
                with open(_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass

    def update_pipeline_stage(self, player_id: int, stage: str, value: bool, ip: str = "") -> None:
        p_key = f"p{player_id}"
        if p_key in self.pipeline_state:
            self.pipeline_state[p_key][stage] = value
            if ip:
                self.pipeline_state[p_key]["ip"] = ip

    def log_control_packet(self, player_id: int, steering: float, throttle: float, brake: float, flags: int) -> None:
        now = time.time()
        self._control_counts[player_id] = self._control_counts.get(player_id, 0) + 1
        self._last_control_state[player_id] = {"steering": steering, "throttle": throttle, "brake": brake, "flags": flags}

        last_time = self._last_control_log.get(player_id, 0.0)
        if now - last_time >= 1.0:
            count = self._control_counts[player_id]
            self.log(
                "CONTROL_STREAM",
                "STREAM_SUMMARY",
                player=player_id,
                packets_last_second=count,
                steering=f"{steering:+.2f}",
                throttle=f"{throttle:.2f}",
                brake=f"{brake:.2f}",
                flags=flags,
            )
            self._last_control_log[player_id] = now
            self._control_counts[player_id] = 0

    def clear_log(self) -> None:
        with self._events_lock:
            self.events.clear()
        try:
            with self._file_lock:
                with open(_LOG_FILE, "w", encoding="utf-8") as f:
                    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"=== MotionDrive Phone Debug Log Cleared: {ts} ===\n")
        except Exception:
            pass

    def get_sanitized_report(self, mode: str = "single", http_port: int = 8765, ws_port: int = 8766) -> str:
        lines = [
            "MotionDrive Phone Connection Debug Report",
            "------------------------------------------",
            f"Mode: {'Single Player' if mode == 'single' else 'Two Player'}",
            f"Local IP: {self.pipeline_state.get('selected_ip', 'Unknown')}",
            f"Network Interface: {self.pipeline_state.get('selected_iface', 'Unknown')}",
            "",
            "Server Status:",
            f"  HTTP: {'● READY' if self.pipeline_state.get('http_ready') else '○ WAITING'} ({self.pipeline_state.get('http_bind', '0.0.0.0:8765')})",
            f"  WebSocket: {'● READY' if self.pipeline_state.get('ws_ready') else '○ WAITING'} ({self.pipeline_state.get('ws_bind', '0.0.0.0:8766')})",
            "",
        ]

        for pid in (1, 2):
            if mode == "single" and pid == 2:
                continue
            pdata = self.pipeline_state.get(f"p{pid}", {})
            lines.extend([
                f"Player {pid} Pipeline:",
                f"  Host IP: {self.pipeline_state.get('selected_ip', 'Unknown')}",
                f"  Client IP: {pdata.get('ip', 'Not Connected')}",
                f"  HTTP Request: {'● CONNECTED' if pdata.get('http') else '○ WAITING'}",
                f"  WebSocket TCP: {'● CONNECTED' if pdata.get('ws') else '○ WAITING'}",
                f"  Handshake ACK: {'● SUCCESS' if pdata.get('handshake') else '○ WAITING'}",
                f"  Session Auth: {'● VALID' if pdata.get('session') else '○ WAITING'}",
                f"  Desktop UI: {'● CONNECTED' if pdata.get('ui') else '○ WAITING'}",
                "",
            ])

        lines.append("Recent Events (Last 15):")
        with self._events_lock:
            recent = self.events[-15:]
            for ev in recent:
                lines.append(f"  {ev['raw']}")

        return "\n".join(lines)

    def run_local_network_test(self, http_port: int = 8765, ws_port: int = 8766) -> Dict[str, Any]:
        """Runs local loopback diagnostic probes to verify HTTP & WebSocket server binding."""
        res = {
            "http": False,
            "ws": False,
            "ip_valid": False,
            "details": [],
        }

        # 1. IP Check
        selected_ip = self.pipeline_state.get("selected_ip", "127.0.0.1")
        if selected_ip and selected_ip not in ("127.0.0.1", "0.0.0.0") and not selected_ip.startswith("169.254."):
            res["ip_valid"] = True
            res["details"].append(f"Local IP: PASS ({selected_ip})")
        else:
            res["details"].append(f"Local IP: WARN ({selected_ip})")

        # 2. HTTP Probing
        try:
            url = f"http://127.0.0.1:{http_port}/health"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    res["http"] = True
                    res["details"].append(f"HTTP Server: PASS (port {http_port})")
                else:
                    res["details"].append(f"HTTP Server: FAIL (status {resp.status})")
        except Exception as e:
            res["details"].append(f"HTTP Server: FAIL ({e})")

        # 3. WS TCP Port Probing
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.5)
            err = s.connect_ex(("127.0.0.1", ws_port))
            s.close()
            if err == 0:
                res["ws"] = True
                res["details"].append(f"WebSocket Server: PASS (port {ws_port})")
            else:
                res["details"].append(f"WebSocket Server: FAIL (port {ws_port} closed)")
        except Exception as e:
            res["details"].append(f"WebSocket Server: FAIL ({e})")

        self.log(
            "SELF_TEST",
            "LOCAL_NETWORK_TEST",
            http="PASS" if res["http"] else "FAIL",
            ws="PASS" if res["ws"] else "FAIL",
            ip="PASS" if res["ip_valid"] else "WARN",
        )

        return res
