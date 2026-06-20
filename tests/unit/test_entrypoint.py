#!/usr/bin/env python3
"""Regression: the daemon must start via `python -m`, not by file path.

A by-path run puts src/proxmox_redfish/ on sys.path and shadows the package,
causing 'cannot import name redfish_core ... circular import'. The systemd unit
uses `python3 -m proxmox_redfish.proxmox_redfish`; this guards that mechanism.
"""

import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
SRC = os.path.join(ROOT, "src")


def _run(args):
    env = dict(os.environ, PYTHONPATH=SRC, REDFISH_LOGGING_ENABLED="false")
    return subprocess.run(args, env=env, cwd=ROOT, capture_output=True, text=True, timeout=30)


def test_module_entrypoint_imports_and_shows_help():
    r = _run([sys.executable, "-m", "proxmox_redfish.proxmox_redfish", "--help"])
    assert r.returncode == 0, r.stderr
    assert "Proxmox Redfish Daemon" in r.stdout
    assert "circular import" not in (r.stdout + r.stderr)


def test_by_path_invocation_is_the_known_trap():
    # Documents the failure mode the -m form avoids (not how we run in production).
    r = _run([sys.executable, os.path.join(SRC, "proxmox_redfish", "proxmox_redfish.py"), "--help"])
    assert r.returncode != 0 and "circular import" in r.stderr
