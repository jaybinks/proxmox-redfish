#!/usr/bin/env python3
"""Serial capture: line framing, opt-in gating, and lazy collector start."""

from unittest.mock import patch

from proxmox_redfish import serial_capture as sc


class TestCaptureGating:
    def test_enabled_by_default(self):
        with patch.dict("os.environ", {}, clear=False) as e:
            e.pop("REDFISH_SERIAL_CAPTURE", None)
            assert sc.capture_enabled() is True

    def test_disable_via_env(self):
        with patch.dict("os.environ", {"REDFISH_SERIAL_CAPTURE": "0"}):
            assert sc.capture_enabled() is False

    def test_ensure_collector_noop_when_disabled(self):
        with patch.dict("os.environ", {"REDFISH_SERIAL_CAPTURE": "0"}):
            assert sc.ensure_collector(9999) is False

    def test_ensure_collector_false_without_socket(self):
        with patch.dict("os.environ", {"REDFISH_SERIAL_CAPTURE": "1"}), patch("os.path.exists", return_value=False):
            assert sc.ensure_collector(9999) is False

    def test_get_lines_empty_when_no_collector(self):
        with patch.dict("os.environ", {"REDFISH_SERIAL_CAPTURE": "0"}):
            assert sc.get_lines(12345) == []


class TestLineFraming:
    def test_ingest_splits_complete_lines_keeps_partial(self):
        c = sc._Collector(1)
        partial = c._ingest(b"hello\r\nworld\npar")
        assert [line for _, line in c.buffer] == ["hello", "world"]
        assert partial == b"par"

    def test_ingest_flushes_overlong_unterminated(self):
        c = sc._Collector(1)
        partial = c._ingest(b"x" * (sc._MAX_LINE_BYTES + 10))
        assert partial == b""
        assert len(c.buffer) == 1

    def test_ring_buffer_bounded(self):
        c = sc._Collector(1)
        c._ingest(("\n".join(f"line{i}" for i in range(sc._MAX_LINES + 50)) + "\n").encode())
        assert len(c.buffer) == sc._MAX_LINES

    def test_socket_path(self):
        assert sc.socket_path(4001).endswith("/4001.serial0")


class TestMultiPort:
    def test_log_id_port_mapping(self):
        assert sc.log_id_for_port(0) == "SerialLog"
        assert sc.log_id_for_port(2) == "SerialLog2"
        assert sc.port_from_log_id("SerialLog") == 0
        assert sc.port_from_log_id("SerialLog3") == 3
        assert sc.port_from_log_id("SerialLog9") is None  # out of range
        assert sc.port_from_log_id("SEL") is None
        assert sc.port_from_log_id("SerialLogX") is None

    def test_socket_path_per_port(self):
        assert sc.socket_path(4001, 0).endswith("/4001.serial0")
        assert sc.socket_path(4001, 3).endswith("/4001.serial3")

    def test_available_ports_reads_sockets(self):
        from unittest.mock import patch

        present = {sc.socket_path(7, 0), sc.socket_path(7, 2)}
        with patch("os.path.exists", side_effect=lambda p: p in present):
            assert sc.available_ports(7) == [0, 2]


class TestLogServiceEnumeration:
    def test_collection_lists_extra_serial_ports(self):
        from unittest.mock import patch

        from proxmox_redfish import redfish_services as rs

        with patch("proxmox_redfish.serial_capture.available_ports", return_value=[0, 1]):
            body, code = rs.build_log_service_collection(4001)
        ids = [m["@odata.id"].split("/")[-1] for m in body["Members"]]
        assert code == 200
        assert "SEL" in ids and "SerialLog" in ids and "SerialLog1" in ids

    def test_serial1_log_service_resolves(self):
        from proxmox_redfish import redfish_services as rs

        body, code = rs.build_log_service(4001, "SerialLog1")
        assert code == 200 and body["Oem"]["Proxmox"]["SerialPort"] == 1

    def test_unknown_log_service_404(self):
        from proxmox_redfish import redfish_services as rs

        _, code = rs.build_log_service(4001, "Bogus")
        assert code == 404
