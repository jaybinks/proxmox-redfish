#!/usr/bin/env python3
"""Unit tests for proxmox_redfish.loki_logging -- Grafana Loki log handler."""

import logging
from unittest.mock import patch

from proxmox_redfish import loki_logging


def _record(msg="hello"):
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)


class TestLokiHandler:
    def test_emit_queues_and_push_payload_shape(self):
        h = loki_logging.LokiHandler(
            "http://loki:3100/loki/api/v1/push", {"job": "test"}, flush_interval=999, batch_size=999
        )
        h.setFormatter(logging.Formatter("%(message)s"))
        try:
            h.emit(_record("line-one"))
            captured = {}

            def fake_post(url, json=None, headers=None, auth=None, timeout=None, verify=None):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers

                class R:
                    status_code = 204

                return R()

            with patch("requests.post", fake_post):
                h.flush()
            assert captured["url"].endswith("/loki/api/v1/push")
            stream = captured["json"]["streams"][0]
            assert stream["stream"]["job"] == "test"
            assert stream["values"][0][1] == "line-one"
            # ns timestamp
            assert stream["values"][0][0].isdigit() and len(stream["values"][0][0]) >= 18
        finally:
            h.close()

    def test_batch_size_triggers_flush(self):
        with patch.object(loki_logging.LokiHandler, "flush") as mock_flush:
            h = loki_logging.LokiHandler("http://x/push", {"job": "t"}, flush_interval=999, batch_size=2)
            h.setFormatter(logging.Formatter("%(message)s"))
            h.emit(_record("a"))
            h.emit(_record("b"))  # reaches batch_size -> flush
            assert mock_flush.called
            h._stop.set()

    def test_emit_never_raises_on_bad_post(self):
        h = loki_logging.LokiHandler("http://x/push", {"job": "t"}, flush_interval=999)
        h.setFormatter(logging.Formatter("%(message)s"))
        try:
            h.emit(_record("x"))
            with patch("requests.post", side_effect=Exception("network down")):
                h.flush()  # must not raise
        finally:
            h.close()

    def test_tenant_header(self):
        h = loki_logging.LokiHandler("http://x/push", {"job": "t"}, tenant="acme", flush_interval=999)
        h.setFormatter(logging.Formatter("%(message)s"))
        try:
            h.emit(_record("x"))
            seen = {}
            with patch("requests.post", lambda *a, **k: seen.update(k) or type("R", (), {"status_code": 204})()):
                h.flush()
            assert seen["headers"]["X-Scope-OrgID"] == "acme"
        finally:
            h.close()


class TestBuildFromEnv:
    def test_returns_none_without_url(self, monkeypatch):
        monkeypatch.delenv("REDFISH_LOKI_URL", raising=False)
        assert loki_logging.build_loki_handler_from_env() is None

    def test_builds_with_env(self, monkeypatch):
        monkeypatch.setenv("REDFISH_LOKI_URL", "http://loki:3100/loki/api/v1/push")
        monkeypatch.setenv("REDFISH_LOKI_LABELS", "job=pr,env=prod")
        monkeypatch.setenv("REDFISH_LOKI_USER", "u")
        monkeypatch.setenv("REDFISH_LOKI_PASSWORD", "p")
        h = loki_logging.build_loki_handler_from_env()
        try:
            assert isinstance(h, loki_logging.LokiHandler)
            assert h.labels["env"] == "prod"
            assert h.auth == ("u", "p")
        finally:
            h.close()
