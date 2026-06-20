#!/usr/bin/env python3
"""
serial_capture.py -- capture a Proxmox VM's serial console into a ring buffer so it
can be surfaced as Redfish LogService/SerialLog entries.

A VM configured with ``serial0: socket`` exposes a QEMU unix socket at
``/run/qemu-server/<vmid>.serial0``. There is no persistent serial history in
Proxmox, so to answer ``GET .../LogServices/SerialLog/Entries`` with real data we run
a small background reader that connects to that socket and appends lines to a bounded
in-memory ring buffer (one per VM). The daemon runs as root on the host and can read
the socket directly.

OPT-IN: enabled only when REDFISH_SERIAL_CAPTURE=1. The QEMU serial socket accepts a
single client, so capturing it means an interactive ``qm terminal <vmid>`` cannot
attach at the same time -- hence off by default. Capture is lazy: a collector for a
VM starts on first access to its SerialLog entries.

History is from first-capture onward and is lost on daemon restart (in-memory by
design -- no host files, no extra perms). Memory is bounded by REDFISH_SERIAL_MAX_LINES.
"""

import logging
import os
import socket
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("proxmox-redfish.serial")

_SOCK_DIR = os.getenv("REDFISH_SERIAL_SOCK_DIR", "/run/qemu-server")
_MAX_LINES = int(os.getenv("REDFISH_SERIAL_MAX_LINES", "2000"))
_MAX_LINE_BYTES = 4096  # guard a single unterminated line from growing without bound

# Proxmox/QEMU supports serial0..serial3.
SERIAL_PORTS = (0, 1, 2, 3)


def capture_enabled() -> bool:
    return os.getenv("REDFISH_SERIAL_CAPTURE", "0") == "1"


def socket_path(vmid: int, port: int = 0) -> str:
    return os.path.join(_SOCK_DIR, f"{vmid}.serial{port}")


def available_ports(vmid: int) -> List[int]:
    """Serial ports whose QEMU socket currently exists for this VM (running)."""
    return [p for p in SERIAL_PORTS if os.path.exists(socket_path(vmid, p))]


def log_id_for_port(port: int) -> str:
    """Redfish LogService id for a serial port (port 0 keeps the legacy 'SerialLog')."""
    return "SerialLog" if port == 0 else f"SerialLog{port}"


def port_from_log_id(log_id: str) -> "Optional[int]":
    if log_id == "SerialLog":
        return 0
    if log_id.startswith("SerialLog"):
        suffix = log_id[len("SerialLog") :]
        if suffix.isdigit() and int(suffix) in SERIAL_PORTS:
            return int(suffix)
    return None


class _Collector:
    """Background reader for one VM serial port socket -> bounded line ring buffer."""

    def __init__(self, vmid: int, port: int = 0) -> None:
        self.vmid = vmid
        self.port = port
        self.buffer: Deque[Tuple[float, str]] = deque(maxlen=_MAX_LINES)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"serial-{vmid}.{port}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        path = socket_path(self.vmid, self.port)
        partial = b""
        while not self._stop.is_set():
            if not os.path.exists(path):  # VM stopped / no serial socket
                time.sleep(2.0)
                continue
            sock = None
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect(path)
                logger.info("serial capture connected for VM %s port %s", self.vmid, self.port)
                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:  # peer closed
                        break
                    partial = self._ingest(partial + chunk)
            except (OSError, socket.error) as exc:  # connect/recv failure -> retry
                logger.debug("serial capture for VM %s port %s: %s", self.vmid, self.port, exc)
                time.sleep(2.0)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    def _ingest(self, data: bytes) -> bytes:
        """Split complete lines into the buffer; return the trailing partial bytes."""
        while b"\n" in data:
            line, data = data.split(b"\n", 1)
            text = line.rstrip(b"\r").decode("utf-8", errors="replace")
            self.buffer.append((time.time(), text))
        if len(data) > _MAX_LINE_BYTES:  # flush an over-long unterminated line
            self.buffer.append((time.time(), data.decode("utf-8", errors="replace")))
            data = b""
        return data


_collectors: Dict[Tuple[int, int], _Collector] = {}
_lock = threading.Lock()


def ensure_collector(vmid: int, port: int = 0) -> bool:
    """
    Start a collector for (vmid, port) if capture is enabled and the serial socket
    exists. Returns True if a collector is (now) running for this port.
    """
    if not capture_enabled():
        return False
    with _lock:
        key = (vmid, port)
        existing = _collectors.get(key)
        if existing and existing.alive():
            return True
        if not os.path.exists(socket_path(vmid, port)):
            return False
        collector = _Collector(vmid, port)
        _collectors[key] = collector
        collector.start()
        return True


def get_lines(vmid: int, port: int = 0) -> List[Tuple[float, str]]:
    """Snapshot of captured (timestamp, line) tuples for (vmid, port) (oldest first)."""
    ensure_collector(vmid, port)
    collector = _collectors.get((vmid, port))
    return list(collector.buffer) if collector else []


def stop_all() -> None:
    with _lock:
        for collector in _collectors.values():
            collector.stop()
        _collectors.clear()
