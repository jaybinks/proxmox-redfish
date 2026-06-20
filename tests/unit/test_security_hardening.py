#!/usr/bin/env python3
"""
Regression tests for the adversarial security hardening pass.

Covers credential redaction in logs, the no-plaintext-password session policy,
SSRF guards on event delivery and ISO fetch, request-body size limits, and the
session/subscription store bounds. These assert the *security* properties; the
existing functional suites assert behaviour.
"""

import pytest

from proxmox_redfish import proxmox_redfish as pr
from proxmox_redfish import redfish_services as rs


# --------------------------------------------------------------------------- #
# Credential redaction in logs
# --------------------------------------------------------------------------- #
class TestRedaction:
    def test_authorization_header_is_redacted(self):
        headers = {"Authorization": "Basic cm9vdEBwYW06c3VwZXJzZWNyZXQ=", "Accept": "application/json"}
        rendered = pr._redact_headers(headers)
        assert "cm9vdEBwYW0" not in rendered
        assert "<redacted>" in rendered
        assert "Accept: application/json" in rendered

    def test_x_auth_token_header_is_redacted(self):
        rendered = pr._redact_headers({"X-Auth-Token": "deadbeefcafef00d"})
        assert "deadbeef" not in rendered
        assert "<redacted>" in rendered

    def test_password_field_in_body_is_redacted(self):
        payload = {"UserName": "root@pam", "Password": "hunter2", "nested": {"Token": "abc"}}
        safe = pr._redact_payload_for_log(payload)
        assert safe["UserName"] == "root@pam"
        assert safe["Password"] == "<redacted>"
        assert safe["nested"]["Token"] == "<redacted>"

    def test_redaction_handles_lists_and_scalars(self):
        assert pr._redact_payload_for_log([{"password": "x"}, "y"]) == [{"password": "<redacted>"}, "y"]
        assert pr._redact_payload_for_log("plain") == "plain"


# --------------------------------------------------------------------------- #
# Session policy: never key/store the password in a way that leaks it
# --------------------------------------------------------------------------- #
class TestSessionPolicy:
    def test_basic_auth_does_not_persist_a_password_keyed_session(self, monkeypatch):
        pr.sessions.clear()
        monkeypatch.setattr(pr, "AUTH", "Basic")
        monkeypatch.setattr(pr, "authenticate_user", lambda u, p: True)
        import base64

        token = base64.b64encode(b"root@pam:supersecret").decode()
        valid, who = pr.validate_token({"Authorization": f"Basic {token}"})
        assert valid and who == "root@pam"
        # No session is created, so the password can never appear in a Sessions URI.
        assert pr.sessions == {}
        for key in pr.sessions:
            assert "supersecret" not in key

    def test_prune_drops_expired_and_bounds_store(self, monkeypatch):
        pr.sessions.clear()
        monkeypatch.setattr(pr, "SESSION_TTL_SECONDS", 100)
        monkeypatch.setattr(pr, "MAX_SESSIONS", 2)
        now = pr.time.time()
        pr.sessions["old"] = {"created": now - 1000, "username": "a", "password": "x"}
        pr.sessions["a"] = {"created": now - 1, "username": "a", "password": "x"}
        pr.sessions["b"] = {"created": now - 2, "username": "b", "password": "y"}
        pr.sessions["c"] = {"created": now - 3, "username": "c", "password": "z"}
        pr._prune_sessions()
        assert "old" not in pr.sessions  # expired
        assert len(pr.sessions) <= 2  # bounded
        pr.sessions.clear()


# --------------------------------------------------------------------------- #
# SSRF: event subscription destinations
# --------------------------------------------------------------------------- #
class TestEventSSRF:
    def test_metadata_ip_blocked(self, monkeypatch):
        monkeypatch.delenv("REDFISH_EVENT_ALLOW_INTERNAL", raising=False)
        assert rs.validate_event_destination("http://169.254.169.254/latest/")[0] is False

    def test_loopback_blocked(self, monkeypatch):
        monkeypatch.delenv("REDFISH_EVENT_ALLOW_INTERNAL", raising=False)
        assert rs.validate_event_destination("http://127.0.0.1/x")[0] is False
        assert rs.validate_event_destination("http://[::1]/x")[0] is False

    def test_private_ip_blocked(self, monkeypatch):
        monkeypatch.delenv("REDFISH_EVENT_ALLOW_INTERNAL", raising=False)
        assert rs.validate_event_destination("https://10.0.0.5/ev")[0] is False
        assert rs.validate_event_destination("http://192.168.1.10:8006/")[0] is False

    def test_bad_scheme_blocked(self):
        assert rs.validate_event_destination("file:///etc/passwd")[0] is False
        assert rs.validate_event_destination("gopher://internal/")[0] is False

    def test_public_hostname_allowed(self):
        assert rs.validate_event_destination("https://listener.example/ev")[0] is True

    def test_opt_in_allows_internal(self, monkeypatch):
        monkeypatch.setenv("REDFISH_EVENT_ALLOW_INTERNAL", "1")
        assert rs.validate_event_destination("http://127.0.0.1/x")[0] is True

    def test_create_subscription_rejects_internal(self, monkeypatch):
        monkeypatch.delenv("REDFISH_EVENT_ALLOW_INTERNAL", raising=False)
        rs.subscriptions.clear()
        _, code = rs.create_subscription({"Destination": "http://169.254.169.254/"})
        assert code == 400
        assert rs.subscriptions == {}

    def test_subscription_store_is_bounded(self, monkeypatch):
        monkeypatch.setattr(rs, "MAX_SUBSCRIPTIONS", 1)
        rs.subscriptions.clear()
        _, c1 = rs.create_subscription({"Destination": "https://a.example/ev"})
        _, c2 = rs.create_subscription({"Destination": "https://b.example/ev"})
        assert c1 == 201
        assert c2 == 507  # cap reached
        rs.subscriptions.clear()

    def test_emit_event_skips_internal_destination(self, monkeypatch):
        rs.subscriptions.clear()
        rs.subscriptions["x"] = {"Destination": "http://127.0.0.1/ev"}
        called = {"n": 0}

        class _Resp:
            status_code = 200

        import requests

        def _fake_post(*a, **k):
            called["n"] += 1
            return _Resp()

        monkeypatch.setattr(requests, "post", _fake_post)
        monkeypatch.delenv("REDFISH_EVENT_ALLOW_INTERNAL", raising=False)
        delivered = rs.emit_event("Id", "msg")
        assert delivered == 0
        assert called["n"] == 0  # never even attempted the POST
        rs.subscriptions.clear()


# --------------------------------------------------------------------------- #
# SSRF: ISO / virtual-media fetch URL
# --------------------------------------------------------------------------- #
class TestIsoSSRF:
    def test_metadata_and_internal_rejected(self, monkeypatch):
        monkeypatch.delenv("REDFISH_ISO_ALLOW_INTERNAL", raising=False)
        for bad in (
            "http://169.254.169.254/latest/",
            "http://127.0.0.1/x.iso",
            "https://10.1.2.3/x.iso",
            "http://192.168.0.1/x.iso",
        ):
            with pytest.raises(ValueError):
                pr._validate_fetch_url(bad)

    def test_bad_scheme_rejected(self):
        with pytest.raises(ValueError):
            pr._validate_fetch_url("file:///etc/passwd")
        with pytest.raises(ValueError):
            pr._validate_fetch_url("ftp://host/x.iso")

    def test_public_hostname_allowed(self):
        pr._validate_fetch_url("https://download.example.com/x.iso")  # no raise

    def test_opt_in_allows_internal(self, monkeypatch):
        monkeypatch.setenv("REDFISH_ISO_ALLOW_INTERNAL", "1")
        pr._validate_fetch_url("http://127.0.0.1/x.iso")  # no raise


def test_module_constants_present():
    # The hardening constants must exist (regression against accidental removal).
    assert pr.MAX_REQUEST_BODY_BYTES > 0
    assert pr.MAX_SESSIONS > 0
    assert pr.SESSION_TTL_SECONDS > 0
    assert "authorization" in pr._SENSITIVE_HEADERS
