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
from typing import Deque, Dict, List, Tuple

logger = logging.getLogger("proxmox-redfish.serial")

_SOCK_DIR = os.getenv("REDFISH_SERIAL_SOCK_DIR", "/run/qemu-server")
_MAX_LINES = int(os.getenv("REDFISH_SERIAL_MAX_LINES", "2000"))
_MAX_LINE_BYTES = 4096  # guard a single unterminated line from growing without bound


def capture_enabled() -> bool:
    return os.getenv("REDFISH_SERIAL_CAPTURE", "0") == "1"


def socket_path(vmid: int) -> str:
    return os.path.join(_SOCK_DIR, f"{vmid}.serial0")


class _Collector:
    """Background reader for one VM's serial socket -> bounded line ring buffer."""

    def __init__(self, vmid: int) -> None:
        self.vmid = vmid
        self.buffer: Deque[Tuple[float, str]] = deque(maxlen=_MAX_LINES)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"serial-{vmid}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        path = socket_path(self.vmid)
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
                logger.info("serial capture connected for VM %s", self.vmid)
                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:  # peer closed
                        break
                    partial = self._ingest(partial + chunk)
            except (OSError, socket.error) as exc:  # connect/recv failure -> retry
                logger.debug("serial capture for VM %s: %s", self.vmid, exc)
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


_collectors: Dict[int, _Collector] = {}
_lock = threading.Lock()


def ensure_collector(vmid: int) -> bool:
    """
    Start a collector for vmid if capture is enabled and the serial socket exists.
    Returns True if a collector is (now) running for this VM.
    """
    if not capture_enabled():
        return False
    with _lock:
        existing = _collectors.get(vmid)
        if existing and existing.alive():
            return True
        if not os.path.exists(socket_path(vmid)):
            return False
        collector = _Collector(vmid)
        _collectors[vmid] = collector
        collector.start()
        return True


def get_lines(vmid: int) -> List[Tuple[float, str]]:
    """Snapshot of captured (timestamp, line) tuples for vmid (oldest first)."""
    ensure_collector(vmid)
    collector = _collectors.get(vmid)
    return list(collector.buffer) if collector else []


def stop_all() -> None:
    with _lock:
        for collector in _collectors.values():
            collector.stop()
        _collectors.clear()
