#!/usr/bin/env python3
"""
The daemon must use the CALLER's Proxmox credentials for operations (so the static
PROXMOX_PASSWORD default 'CHANGE_ME' is irrelevant). These tests pin that behaviour.
"""

import base64
from unittest.mock import patch

from proxmox_redfish import proxmox_redfish as m


def _basic(user, password):
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


class TestClientCredentials:
    def test_basic_plain_user_gets_pam_realm(self):
        assert m._client_credentials(_basic("root", "sekret")) == ("root@pam", "sekret")

    def test_basic_with_realm_preserved(self):
        assert m._client_credentials(_basic("admin@pve", "pw")) == ("admin@pve", "pw")

    def test_basic_api_token_id_preserved(self):
        creds = m._client_credentials(_basic("root@pam!redfish", "uuid-secret"))
        assert creds == ("root@pam!redfish", "uuid-secret")

    def test_session_token_credentials(self):
        m.sessions["tok"] = {"username": "u@pam", "password": "p"}
        try:
            assert m._client_credentials({"X-Auth-Token": "tok"}) == ("u@pam", "p")
        finally:
            del m.sessions["tok"]

    def test_no_credentials(self):
        assert m._client_credentials({}) is None


class TestGetProxmoxApiPassthrough:
    def test_uses_caller_password_not_static(self):
        with patch.object(m, "validate_token", return_value=(True, "root@pam")), patch.object(m, "ProxmoxAPI") as PA:
            m.get_proxmox_api(_basic("root", "caller-pw"))
        kwargs = PA.call_args.kwargs
        assert kwargs["user"] == "root@pam"
        assert kwargs["password"] == "caller-pw"  # the caller's, not PROXMOX_PASSWORD

    def test_api_token_uses_token_kwargs(self):
        with patch.object(m, "validate_token", return_value=(True, "root@pam!t")), patch.object(m, "ProxmoxAPI") as PA:
            m.get_proxmox_api(_basic("root@pam!redfish", "the-uuid"))
        kwargs = PA.call_args.kwargs
        assert kwargs["user"] == "root@pam"
        assert kwargs["token_name"] == "redfish"
        assert kwargs["token_value"] == "the-uuid"
        assert "password" not in kwargs

    def test_falls_back_to_static_when_no_creds(self):
        with patch.object(m, "validate_token", return_value=(True, "x")), patch.object(m, "ProxmoxAPI") as PA:
            m.get_proxmox_api({})  # no Authorization header
        assert PA.call_args.kwargs["user"] == m.PROXMOX_USER
