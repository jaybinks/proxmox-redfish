#!/usr/bin/env python3
"""
Structural Redfish conformance harness.

Crawls every implemented resource through the real HTTP handler and asserts the
Redfish structural invariants a schema validator checks: @odata.id / @odata.type
presence and shape, collection Members + count consistency, singleton Id, the
OData-Version header, an ETag on 200s, well-formed $metadata XML, and a valid
odata service document.

This is a lightweight, offline stand-in for the DMTF Redfish-Service-Validator
(see docs/research/redfish-validation-tools.md) — it does not replace a full CSDL
crawl but guarantees the daemon's own emitted shapes stay conformant in CI.
"""

import json
import os
import re
import sys
import unittest
import xml.dom.minidom
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from proxmox_redfish import hostops  # noqa: E402
from proxmox_redfish.proxmox_redfish import RedfishRequestHandler  # noqa: E402

TYPE_RE = re.compile(r"^#[A-Za-z]+(\.v\d+_\d+_\d+)?\.[A-Za-z]+$")

# Resources to crawl. SecureBoot needs a mocked efidisk (no host shell-out).
CRAWL = [
    "/redfish/v1",
    "/redfish/v1/Systems",
    "/redfish/v1/Systems/100",
    "/redfish/v1/Systems/100/Memory",
    "/redfish/v1/Systems/100/Memory/DRAM",
    "/redfish/v1/Systems/100/SecureBoot",
    "/redfish/v1/Systems/100/SecureBoot/SecureBootDatabases",
    "/redfish/v1/Systems/100/SecureBoot/SecureBootDatabases/PK",
    "/redfish/v1/Chassis",
    "/redfish/v1/Chassis/100",
    "/redfish/v1/Chassis/100/Power",
    "/redfish/v1/Chassis/100/Thermal",
    "/redfish/v1/Managers/100",
    "/redfish/v1/SessionService",
    "/redfish/v1/SessionService/Sessions",
    "/redfish/v1/TaskService",
    "/redfish/v1/AccountService",
    "/redfish/v1/AccountService/Accounts",
    "/redfish/v1/AccountService/Roles",
    "/redfish/v1/AccountService/Roles/Administrator",
    "/redfish/v1/EventService",
    "/redfish/v1/EventService/Subscriptions",
    "/redfish/v1/UpdateService",
    "/redfish/v1/Registries",
    "/redfish/v1/JsonSchemas",
]


def _mock_proxmox():
    p = MagicMock()
    node = p.nodes.return_value
    node.qemu.get.return_value = [{"vmid": 100, "name": "vm"}]
    qemu = node.qemu.return_value
    qemu.status.current.get.return_value = {"status": "running"}
    qemu.config.get.return_value = {"name": "vm", "memory": 2048, "bios": "ovmf", "cores": 2}
    p.access.users.get.return_value = [{"userid": "root@pam", "enable": 1}]
    return p


def _get(path):
    request = MagicMock()
    request.makefile.return_value = BytesIO(b"")
    h = RedfishRequestHandler(request, ("127.0.0.1", 8000), None)
    h.wfile = BytesIO()
    h.command = "GET"
    h.path = path
    h.requestline = f"GET {path} HTTP/1.1"
    h.request_version = "HTTP/1.1"
    h.headers = {"X-Auth-Token": "tok"}
    h.rfile = BytesIO(b"")
    h.do_GET()
    raw = h.wfile.getvalue().decode("utf-8", errors="replace")
    head, _, body = raw.partition("\r\n\r\n")
    status = int(head.splitlines()[0].split()[1])
    headers = {}
    for line in head.splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers, body


class ConformanceTests(unittest.TestCase):
    def setUp(self):
        self.proxmox = _mock_proxmox()
        efi = hostops.EfiDisk("local-lvm:vm-100-disk-0", "local-lvm", "/dev/pve/vm-100-disk-0", "4m", True, 540672)
        patches = [
            patch("proxmox_redfish.proxmox_redfish.validate_token", return_value=(True, "admin@pam")),
            patch("proxmox_redfish.proxmox_redfish.get_proxmox_api", return_value=self.proxmox),
            patch("proxmox_redfish.secureboot.hostops.locate_efidisk", return_value=efi),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _assert_resource(self, body, path):
        self.assertIn("@odata.id", body, f"{path} missing @odata.id")
        self.assertTrue(body["@odata.id"].startswith("/redfish/v1"), f"{path} bad @odata.id")
        self.assertIn("@odata.type", body, f"{path} missing @odata.type")
        otype = body["@odata.type"]
        self.assertRegex(otype, TYPE_RE, f"{path} bad @odata.type {otype}")
        if otype.endswith("Collection"):
            self.assertIsInstance(body.get("Members"), list, f"{path} collection without Members")
            self.assertEqual(body["Members@odata.count"], len(body["Members"]), f"{path} Members count mismatch")
            for m in body["Members"]:
                self.assertIn("@odata.id", m)
        else:
            self.assertIn("Id", body, f"{path} singleton without Id")

    def test_crawl_all_resources_conformant(self):
        for path in CRAWL:
            status, headers, raw = _get(path)
            self.assertEqual(status, 200, f"{path} -> {status}")
            self.assertEqual(headers.get("odata-version"), "4.0", f"{path} missing OData-Version")
            self.assertIn("etag", headers, f"{path} missing ETag")
            body = json.loads(raw)
            self._assert_resource(body, path)

    def test_metadata_is_well_formed_xml(self):
        status, headers, raw = _get("/redfish/v1/$metadata")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "application/xml")
        doc = xml.dom.minidom.parseString(raw)  # raises if malformed
        self.assertEqual(doc.documentElement.tagName, "edmx:Edmx")

    def test_odata_service_doc(self):
        status, _, raw = _get("/redfish/v1/odata")
        self.assertEqual(status, 200)
        body = json.loads(raw)
        self.assertIn("value", body)
        self.assertTrue(any(e["name"] == "Systems" for e in body["value"]))

    def test_error_envelope_shape(self):
        status, _, raw = _get("/redfish/v1/Systems/100/NoSuchResource")
        self.assertEqual(status, 404)
        body = json.loads(raw)
        self.assertIn("error", body)
        self.assertIn("code", body["error"])


if __name__ == "__main__":
    unittest.main()
