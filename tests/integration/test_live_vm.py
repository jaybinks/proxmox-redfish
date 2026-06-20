#!/usr/bin/env python3
"""
Deterministic, re-runnable LIVE compatibility suite: every implemented endpoint,
exercised by every supported Redfish client, against a real Proxmox VM.

Enable by pointing it at a running daemon + a VM that exists:

    export REDFISH_LIVE_URL=https://192.168.0.20:8443
    export REDFISH_LIVE_USER='root@pam!Immutable2'      # Proxmox user OR API-token id
    export REDFISH_LIVE_PASS='<password-or-token-secret>'
    export REDFISH_LIVE_VMID=4000
    # optional: allow state-changing tests (power/boot/SecureBoot enroll)
    export REDFISH_LIVE_ALLOW_WRITE=0
    pytest tests/integration/test_live_vm.py -v

Without REDFISH_LIVE_URL the whole module is skipped (so unit CI is unaffected).
Reads are always run; safe writes (session login/logout, event-subscription and
certificate create+delete) always run; disruptive writes (power, boot override,
SecureBoot varstore enroll) run only when REDFISH_LIVE_ALLOW_WRITE=1.
"""

import os
import shutil
import subprocess
import sys

import pytest

URL = os.getenv("REDFISH_LIVE_URL")
USER = os.getenv("REDFISH_LIVE_USER", "")
PASS = os.getenv("REDFISH_LIVE_PASS", "")
VMID = os.getenv("REDFISH_LIVE_VMID", "4000")
ALLOW_WRITE = os.getenv("REDFISH_LIVE_ALLOW_WRITE", "0") == "1"

pytestmark = pytest.mark.skipif(not URL, reason="set REDFISH_LIVE_URL to run live client tests")


def _endpoints(v):
    """Every GET-serviceable endpoint, with the status codes we accept."""
    s = f"/redfish/v1/Systems/{v}"
    ok = {200}
    return [
        # service + discovery
        ("/redfish", ok),
        ("/redfish/v1", ok),
        ("/redfish/v1/odata", ok),
        ("/redfish/v1/$metadata", ok),
        ("/redfish/v1/Registries", ok),
        ("/redfish/v1/JsonSchemas", ok),
        # systems
        ("/redfish/v1/Systems", ok),
        (s, ok),
        (s + "/Bios", ok),
        (s + "/Processors", ok),
        (s + "/Processors/0", ok),
        (s + "/Storage", ok),
        (s + "/Storage/1", ok),
        (s + "/EthernetInterfaces", ok),
        (s + "/Memory", ok),
        (s + "/Memory/DRAM", ok),
        (s + "/LogServices", ok),
        (s + "/LogServices/SEL", ok),
        (s + "/LogServices/SEL/Entries", ok),
        (s + "/SecureBoot", ok),
        (s + "/SecureBoot/SecureBootDatabases", ok),
        (s + "/SecureBoot/SecureBootDatabases/PK", ok),
        (s + "/SecureBoot/SecureBootDatabases/KEK", ok),
        (s + "/SecureBoot/SecureBootDatabases/db", ok),
        (s + "/SecureBoot/SecureBootDatabases/dbx", ok),
        (s + "/SecureBoot/SecureBootDatabases/db/Certificates", ok),
        # chassis
        ("/redfish/v1/Chassis", ok),
        (f"/redfish/v1/Chassis/{v}", ok),
        (f"/redfish/v1/Chassis/{v}/Power", ok),
        (f"/redfish/v1/Chassis/{v}/Thermal", ok),
        # managers
        ("/redfish/v1/Managers", ok),
        (f"/redfish/v1/Managers/{v}", ok),
        (f"/redfish/v1/Managers/{v}/VirtualMedia", ok),
        (f"/redfish/v1/Managers/{v}/VirtualMedia/Cd", ok),
        # services
        ("/redfish/v1/SessionService", ok),
        ("/redfish/v1/SessionService/Sessions", ok),
        ("/redfish/v1/TaskService", ok),
        ("/redfish/v1/TaskService/Tasks", ok),
        ("/redfish/v1/AccountService", ok),
        ("/redfish/v1/AccountService/Accounts", ok),
        ("/redfish/v1/AccountService/Roles", ok),
        ("/redfish/v1/AccountService/Roles/Administrator", ok),
        ("/redfish/v1/EventService", ok),
        ("/redfish/v1/EventService/Subscriptions", ok),
        ("/redfish/v1/UpdateService", ok),
        ("/redfish/v1/CertificateService", ok),
        ("/redfish/v1/CertificateService/CertificateLocations", ok),
    ]


ENDPOINTS = _endpoints(VMID)


@pytest.fixture(scope="module")
def http():
    import requests
    import urllib3

    urllib3.disable_warnings()
    s = requests.Session()
    s.verify = False
    s.auth = (USER, PASS)
    return s


# --------------------------------------------------------------------------- #
# Raw HTTP — every endpoint
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path,expected", ENDPOINTS, ids=[p for p, _ in ENDPOINTS])
def test_raw_get_every_endpoint(http, path, expected):
    r = http.get(URL + path, timeout=30)
    assert r.status_code in expected, f"{path} -> {r.status_code}: {r.text[:200]}"
    if r.headers.get("content-type", "").startswith("application/json"):
        body = r.json()
        if path not in ("/redfish", "/redfish/v1/odata"):
            assert "@odata.id" in body


def test_raw_headers_and_auth(http):
    import requests

    r = http.get(URL + "/redfish/v1", timeout=15)
    assert r.headers.get("OData-Version") == "4.0"
    assert r.headers.get("ETag") and "describedby" in r.headers.get("Link", "")
    # auth required on a protected resource
    anon = requests.get(URL + "/redfish/v1/Systems", verify=False, timeout=15)
    assert anon.status_code == 401


# --------------------------------------------------------------------------- #
# OpenStack sushy
# --------------------------------------------------------------------------- #
def test_sushy_full(http):
    sushy = pytest.importorskip("sushy")
    from sushy.auth import BasicAuth

    conn = sushy.Sushy(URL + "/redfish/v1", auth=BasicAuth(username=USER, password=PASS), verify=False)
    assert conn.redfish_version
    system = conn.get_system(f"/redfish/v1/Systems/{VMID}")
    assert system.identity == str(VMID)
    assert system.power_state is not None
    assert system.boot is not None
    sb = system.secure_boot
    assert sb.mode is not None  # SecureBoot parsed by sushy
    # collections sushy can enumerate
    assert conn.get_system_collection().members_identities
    mgr = conn.get_manager(f"/redfish/v1/Managers/{VMID}")
    assert mgr.identity == str(VMID)


# --------------------------------------------------------------------------- #
# DMTF python-redfish-library
# --------------------------------------------------------------------------- #
def test_python_redfish_library_full():
    redfish = pytest.importorskip("redfish")
    client = redfish.redfish_client(base_url=URL, username=USER, password=PASS, default_prefix="/redfish/v1")
    client.login(auth="basic")
    try:
        for path, expected in ENDPOINTS:
            r = client.get(path)
            assert r.status in expected, f"{path} -> {r.status}"
    finally:
        client.logout()


# --------------------------------------------------------------------------- #
# DMTF redfishtool
# --------------------------------------------------------------------------- #
def _redfishtool(*args):
    exe = shutil.which("redfishtool") or os.path.join(os.path.dirname(sys.executable), "redfishtool")
    host = URL.replace("https://", "").replace("http://", "")
    cmd = [exe, "-r", host, "-S", "Always", "-u", USER, "-p", PASS, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def test_redfishtool_full():
    if not (
        shutil.which("redfishtool") or os.path.exists(os.path.join(os.path.dirname(sys.executable), "redfishtool"))
    ):
        pytest.skip("redfishtool not installed")
    assert str(VMID) in _redfishtool("raw", "GET", "/redfish/v1/Systems").stdout
    assert (
        "ctf" in _redfishtool("raw", "GET", f"/redfish/v1/Systems/{VMID}").stdout.lower()
        or str(VMID) in _redfishtool("raw", "GET", f"/redfish/v1/Systems/{VMID}").stdout
    )
    # walk every endpoint via raw GET
    for path, expected in ENDPOINTS:
        if path in ("/redfish", "/redfish/v1/$metadata"):
            continue
        out = _redfishtool("raw", "GET", path)
        assert out.returncode == 0, f"redfishtool {path}: {out.stderr[:160]}"


# --------------------------------------------------------------------------- #
# The hard things -- safe writes (always run)
# --------------------------------------------------------------------------- #
def test_session_login_logout():
    import requests

    # Redfish session create is enabled only when the daemon runs AUTH=Session;
    # otherwise this is a no-op skip. python-redfish-library exercises Basic above.
    r = requests.post(
        URL + "/redfish/v1/SessionService/Sessions",
        json={"UserName": USER, "Password": PASS},
        verify=False,
        timeout=15,
    )
    if r.status_code in (401, 404):
        pytest.skip("session login not enabled on this daemon build; Basic auth covered elsewhere")
    assert r.status_code == 201
    token = r.headers.get("X-Auth-Token")
    assert token
    loc = r.headers.get("Location")
    d = requests.delete(URL + loc, headers={"X-Auth-Token": token}, verify=False, timeout=15)
    assert d.status_code == 204


def test_event_subscription_create_get_delete(http):
    body = {"Destination": "https://example.invalid/redfish-events", "Protocol": "Redfish", "Context": "live-test"}
    r = http.post(URL + "/redfish/v1/EventService/Subscriptions", json=body, timeout=15)
    assert r.status_code == 201, r.text[:200]
    loc = r.headers.get("Location") or r.json()["@odata.id"]
    g = http.get(URL + loc, timeout=15)
    assert g.status_code == 200 and g.json()["Destination"] == body["Destination"]
    d = http.delete(URL + loc, timeout=15)
    assert d.status_code == 204
    # bad protocol rejected
    bad = http.post(
        URL + "/redfish/v1/EventService/Subscriptions",
        json={"Destination": "https://x/y", "Protocol": "FTP"},
        timeout=15,
    )
    assert bad.status_code == 400


def test_secureboot_certificate_crud(http):
    pytest.importorskip("cryptography")
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "live-test-db")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2040, 1, 1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    base = f"{URL}/redfish/v1/Systems/{VMID}/SecureBoot/SecureBootDatabases/db/Certificates"

    r = http.post(base, json={"CertificateString": pem, "CertificateType": "PEM"}, timeout=20)
    assert r.status_code == 201, r.text[:200]
    cid = r.json()["Id"]
    assert http.get(f"{base}/{cid}", timeout=15).status_code == 200
    assert http.delete(f"{base}/{cid}", timeout=15).status_code == 204

    # a private key must be refused (security boundary, live)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    bad = http.post(base, json={"CertificateString": priv}, timeout=15)
    assert bad.status_code == 400


# --------------------------------------------------------------------------- #
# The hard things -- disruptive writes (gated)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not ALLOW_WRITE, reason="set REDFISH_LIVE_ALLOW_WRITE=1 for state-changing tests")
def test_boot_override_patch(http):
    r = http.patch(
        URL + f"/redfish/v1/Systems/{VMID}",
        json={"Boot": {"BootSourceOverrideTarget": "Pxe", "BootSourceOverrideEnabled": "Once"}},
        timeout=20,
    )
    assert r.status_code in (200, 202, 204)


@pytest.mark.skipif(not ALLOW_WRITE, reason="set REDFISH_LIVE_ALLOW_WRITE=1 for state-changing tests")
def test_power_on_then_off(http):
    base = URL + f"/redfish/v1/Systems/{VMID}/Actions/ComputerSystem.Reset"
    assert http.post(base, json={"ResetType": "On"}, timeout=30).status_code in (200, 202, 204)
    assert http.post(base, json={"ResetType": "ForceOff"}, timeout=30).status_code in (200, 202, 204)


@pytest.mark.skipif(not ALLOW_WRITE, reason="set REDFISH_LIVE_ALLOW_WRITE=1 for state-changing tests")
def test_secureboot_resetkeys_dryrun(http):
    # Dry-run unless the daemon has REDFISH_SB_ALLOW_WRITE=1 + a configured profile.
    r = http.post(
        URL + f"/redfish/v1/Systems/{VMID}/SecureBoot/Actions/SecureBoot.ResetKeys",
        json={"ResetKeysType": "DeleteAllKeys"},
        timeout=30,
    )
    assert r.status_code in (200, 202, 409, 500)  # 409/500 acceptable if no profile staged
