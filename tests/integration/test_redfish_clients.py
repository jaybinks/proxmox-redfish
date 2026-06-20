#!/usr/bin/env python3
"""
Cross-client compatibility tests: prove the daemon works with three independent
Redfish clients driving the live (mock-backed) server over HTTP:

  * OpenStack **sushy**            (the Ironic client library)
  * DMTF **python-redfish-library** (`redfish` on PyPI)
  * DMTF **redfishtool**           (reference CLI)

The server runs in LENIENT protocol mode (MOCK_STRICT=0), the real-world default,
to demonstrate flexible client acceptance. Each client is skipped if not installed.

Run: pytest tests/integration/test_redfish_clients.py -v
"""

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    env = dict(os.environ, REDFISH_LOGGING_ENABLED="false", MOCK_STRICT="0", PROXMOX_NODE="pve")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "tools", "mock_server.py"), str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    up = False
    for _ in range(60):
        if proc.poll() is not None:
            break
        try:
            urllib.request.urlopen(base + "/redfish/v1", timeout=1)
            up = True
            break
        except Exception:
            time.sleep(0.25)
    if not up:
        out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        proc.terminate()
        pytest.fail(f"mock server did not start:\n{out}")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


# --------------------------------------------------------------------------- #
# OpenStack sushy
# --------------------------------------------------------------------------- #
def test_sushy(server):
    sushy = pytest.importorskip("sushy")
    # sushy adds its own /redfish/v1 root prefix; pass the bare base URL.
    conn = sushy.Sushy(server, username="admin", password="admin", verify=False)
    system = conn.get_system("/redfish/v1/Systems/100")
    assert system.power_state is not None
    assert system.identity == "100"
    # Boot info is parseable.
    assert system.boot is not None
    # Drive a real power action through sushy (ComputerSystem.Reset -> 202 Task).
    system.reset_system(sushy.RESET_FORCE_RESTART)


# --------------------------------------------------------------------------- #
# DMTF python-redfish-library
# --------------------------------------------------------------------------- #
def test_python_redfish_library(server):
    redfish = pytest.importorskip("redfish")
    client = redfish.redfish_client(base_url=server, username="admin", password="admin", default_prefix="/redfish/v1")
    client.login(auth="session")
    try:
        root = client.get("/redfish/v1")
        assert root.status == 200
        assert root.dict["RedfishVersion"] == "1.18.0"
        sysresp = client.get("/redfish/v1/Systems/100")
        assert sysresp.status == 200
        assert sysresp.dict["Id"] == "100"
        sb = client.get("/redfish/v1/Systems/100/SecureBoot")
        assert sb.status == 200 and "SecureBootEnable" in sb.dict
    finally:
        client.logout()


# --------------------------------------------------------------------------- #
# DMTF redfishtool CLI
# --------------------------------------------------------------------------- #
def test_redfishtool(server):
    exe = shutil.which("redfishtool") or os.path.join(os.path.dirname(sys.executable), "redfishtool")
    if not os.path.exists(exe):
        pytest.skip("redfishtool not installed")
    host = server.replace("http://", "")
    out = subprocess.check_output(
        [exe, "-r", host, "-S", "Never", "-u", "admin", "-p", "admin", "Systems", "list"],
        stderr=subprocess.STDOUT,
        timeout=60,
    ).decode(errors="replace")
    assert "100" in out
