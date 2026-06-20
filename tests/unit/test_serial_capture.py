#!/usr/bin/env python3
"""Serial capture: line framing, opt-in gating, and lazy collector start."""

from unittest.mock import patch

from proxmox_redfish import serial_capture as sc


class TestCaptureGating:
    def test_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=False) as e:
            e.pop("REDFISH_SERIAL_CAPTURE", None)
            assert sc.capture_enabled() is False

    def test_enabled_via_env(self):
        with patch.dict("os.environ", {"REDFISH_SERIAL_CAPTURE": "1"}):
            assert sc.capture_enabled() is True

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
