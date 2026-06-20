#!/usr/bin/env python3
"""
OData query parameter tests: $select, $top, $skip, pagination nextLink, and the
ServiceRoot ProtocolFeaturesSupported declaration. Unit (apply_query) + handler.
"""

import json
import os
import sys
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from proxmox_redfish import redfish_core  # noqa: E402
from proxmox_redfish.proxmox_redfish import RedfishRequestHandler  # noqa: E402


class TestApplyQuery:
    def _collection(self, n):
        return {
            "@odata.id": "/redfish/v1/Systems",
            "@odata.type": "#ComputerSystemCollection.ComputerSystemCollection",
            "Name": "Systems",
            "Members@odata.count": n,
            "Members": [{"@odata.id": f"/redfish/v1/Systems/{i}"} for i in range(n)],
        }

    def test_select_projects_properties(self):
        body = {"@odata.id": "/x", "@odata.type": "#T.T", "Id": "1", "Name": "n", "Extra": "drop"}
        out = redfish_core.apply_query(body, {"$select": "Name"}, "/x")
        assert "Name" in out and "Extra" not in out
        assert "@odata.id" in out and "Id" in out  # required keys retained

    def test_top_truncates_and_sets_nextlink(self):
        out = redfish_core.apply_query(self._collection(10), {"$top": "3"}, "/redfish/v1/Systems")
        assert len(out["Members"]) == 3
        assert out["Members@odata.count"] == 10  # total preserved
        assert out["Members@odata.nextLink"] == "/redfish/v1/Systems?$skip=3&$top=3"

    def test_skip_offsets(self):
        out = redfish_core.apply_query(self._collection(5), {"$skip": "2"}, "/redfish/v1/Systems")
        assert out["Members"][0]["@odata.id"].endswith("/2")
        assert "Members@odata.nextLink" not in out  # no more pages

    def test_top_skip_combined(self):
        out = redfish_core.apply_query(self._collection(10), {"$skip": "2", "$top": "3"}, "/redfish/v1/Systems")
        ids = [m["@odata.id"].split("/")[-1] for m in out["Members"]]
        assert ids == ["2", "3", "4"]
        assert out["Members@odata.nextLink"].startswith("/redfish/v1/Systems?$skip=5")

    def test_last_page_no_nextlink(self):
        out = redfish_core.apply_query(self._collection(4), {"$skip": "2", "$top": "5"}, "/redfish/v1/Systems")
        assert len(out["Members"]) == 2 and "Members@odata.nextLink" not in out

    def test_bad_values_ignored(self):
        out = redfish_core.apply_query(self._collection(3), {"$top": "abc", "$skip": "-1"}, "/redfish/v1/Systems")
        assert len(out["Members"]) == 3


class TestProtocolFeatures:
    def test_service_root_declares_features(self):
        root = redfish_core.build_service_root()
        pf = root["ProtocolFeaturesSupported"]
        assert pf["SelectQuery"] is True
        assert pf["FilterQuery"] is False
        assert "ExpandQuery" in pf


class TestHandlerQuery:
    def _get(self, path, proxmox):
        with patch("proxmox_redfish.proxmox_redfish.validate_token", return_value=(True, "u")), patch(
            "proxmox_redfish.proxmox_redfish.get_proxmox_api", return_value=proxmox
        ):
            request = MagicMock()
            request.makefile.return_value = BytesIO(b"")
            h = RedfishRequestHandler(request, ("127.0.0.1", 8000), None)
            h.wfile = BytesIO()
            h.command = "GET"
            h.path = path
            h.requestline = f"GET {path} HTTP/1.1"
            h.request_version = "HTTP/1.1"
            h.headers = {"X-Auth-Token": "t"}
            h.rfile = BytesIO(b"")
            h.do_GET()
        raw = h.wfile.getvalue().decode()
        return json.loads(raw[raw.find("\r\n\r\n") + 4 :])

    def test_systems_collection_pagination(self):
        proxmox = MagicMock()
        proxmox.nodes.return_value.qemu.get.return_value = [{"vmid": i} for i in range(5)]
        body = self._get("/redfish/v1/Systems?$top=2", proxmox)
        assert len(body["Members"]) == 2
        assert body["Members@odata.count"] == 5
        assert "Members@odata.nextLink" in body
