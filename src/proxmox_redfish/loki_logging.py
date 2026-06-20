#!/usr/bin/env python3
"""
loki_logging.py -- a Grafana Loki logging handler for proxmox-redfish.

Pushes log records to a Loki HTTP endpoint (`/loki/api/v1/push`) over plain HTTP(S),
independent of the host OS (no syslog/journald dependency). Records are batched and
flushed by a background daemon thread; delivery is best-effort and never blocks or
crashes the daemon. Configuration is entirely via environment variables.

Env:
  REDFISH_LOKI_URL       Loki push URL, e.g. http://loki:3100/loki/api/v1/push  (enables it)
  REDFISH_LOKI_LABELS    comma list of label=value pairs (default: job=proxmox-redfish)
  REDFISH_LOKI_USER      basic-auth user (optional)
  REDFISH_LOKI_PASSWORD  basic-auth password (optional)
  REDFISH_LOKI_TENANT    multi-tenant org id -> X-Scope-OrgID header (optional)
  REDFISH_LOKI_VERIFY    "true"/"false" TLS verification (default: true)
  REDFISH_LOKI_FLUSH     flush interval seconds (default: 3)
  REDFISH_LOKI_BATCH     max queued records before a forced flush (default: 100)
"""

import logging
import os
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple


def _parse_labels(raw: str) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            labels[k.strip()] = v.strip()
    return labels


class LokiHandler(logging.Handler):
    """Batching, non-blocking logging handler that pushes to Grafana Loki."""

    def __init__(
        self,
        url: str,
        labels: Optional[Dict[str, str]] = None,
        *,
        auth: Optional[Tuple[str, str]] = None,
        tenant: Optional[str] = None,
        verify: bool = True,
        flush_interval: float = 3.0,
        batch_size: int = 100,
        timeout: float = 5.0,
    ) -> None:
        super().__init__()
        self.url = url
        self.labels = labels or {"job": "proxmox-redfish"}
        self.labels.setdefault("host", socket.gethostname())
        self.auth = auth
        self.tenant = tenant
        self.verify = verify
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self.timeout = timeout
        self._queue: List[Tuple[str, str]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="loki-flush", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            ts = str(int(time.time() * 1_000_000_000))  # Loki wants ns timestamps
            with self._lock:
                self._queue.append((ts, line))
                over = len(self._queue) >= self.batch_size
            if over:
                self.flush()
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)

    def _run(self) -> None:
        while not self._stop.wait(self.flush_interval):
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._queue:
                return
            batch = self._queue
            self._queue = []
        self._push(batch)

    def _push(self, values: List[Tuple[str, str]]) -> None:
        # Imported lazily so the module is import-safe without requests installed.
        try:
            import requests
        except ImportError:
            return
        payload = {"streams": [{"stream": self.labels, "values": [[ts, line] for ts, line in values]}]}
        headers = {"Content-Type": "application/json"}
        if self.tenant:
            headers["X-Scope-OrgID"] = self.tenant
        try:
            requests.post(
                self.url,
                json=payload,
                headers=headers,
                auth=self.auth,
                timeout=self.timeout,
                verify=self.verify,
            )
        except Exception:  # noqa: BLE001 - delivery is best-effort  # nosec B110
            pass

    def close(self) -> None:
        self._stop.set()
        try:
            self.flush()
        finally:
            super().close()


def build_loki_handler_from_env() -> Optional[LokiHandler]:
    """Construct a LokiHandler from REDFISH_LOKI_* env, or None if not configured."""
    url = os.getenv("REDFISH_LOKI_URL")
    if not url:
        return None
    labels = _parse_labels(os.getenv("REDFISH_LOKI_LABELS", "job=proxmox-redfish"))
    user = os.getenv("REDFISH_LOKI_USER")
    password = os.getenv("REDFISH_LOKI_PASSWORD")
    auth = (user, password) if user and password else None
    handler = LokiHandler(
        url,
        labels,
        auth=auth,
        tenant=os.getenv("REDFISH_LOKI_TENANT") or None,
        verify=os.getenv("REDFISH_LOKI_VERIFY", "true").lower() == "true",
        flush_interval=float(os.getenv("REDFISH_LOKI_FLUSH", "3")),
        batch_size=int(os.getenv("REDFISH_LOKI_BATCH", "100")),
    )
    return handler
