#!/usr/bin/env python3
"""
Phase 4 tests: SecureBoot certificate validation (security), staging CRUD, and
dynamic varstore build. Security-critical: private key material must always be
rejected (INV-13). Uses real self-signed certs via cryptography.
"""

import datetime
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from proxmox_redfish import hostops, secureboot

# Generate one keypair/cert for the whole module (RSA gen is slow).
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _cert_pem(cn="test-pk"):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(_KEY.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2040, 1, 1))
        .sign(_KEY, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _private_key_pem():
    return _KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


# --------------------------------------------------------------------------- #
# validate_public_certificate -- security boundary (INV-13)
# --------------------------------------------------------------------------- #
class TestCertValidation:
    def test_accepts_valid_public_cert(self):
        pem = _cert_pem()
        assert hostops.validate_public_certificate(pem, "PEM") == pem

    def test_rejects_pkcs8_private_key(self):
        with pytest.raises(hostops.PrivateKeyRejectedError):
            hostops.validate_public_certificate(_private_key_pem(), "PEM")

    @pytest.mark.parametrize(
        "blob",
        [
            "-----BEGIN RSA PRIVATE KEY-----\nMII...\n-----END RSA PRIVATE KEY-----",
            "-----BEGIN EC PRIVATE KEY-----\nx\n-----END EC PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----",
        ],
    )
    def test_rejects_any_private_marker(self, blob):
        with pytest.raises(hostops.PrivateKeyRejectedError):
            hostops.validate_public_certificate(blob, "PEM")

    def test_rejects_cert_bundled_with_key(self):
        # A cert + private key in one blob must still be rejected.
        combined = _cert_pem() + "\n" + _private_key_pem()
        with pytest.raises(hostops.PrivateKeyRejectedError):
            hostops.validate_public_certificate(combined, "PEM")

    def test_rejects_empty(self):
        with pytest.raises(hostops.CertificateInvalidError):
            hostops.validate_public_certificate("", "PEM")

    def test_rejects_bad_type(self):
        with pytest.raises(hostops.CertificateInvalidError):
            hostops.validate_public_certificate(_cert_pem(), "JWK")

    def test_rejects_oversized(self):
        big = "-----BEGIN CERTIFICATE-----\n" + ("A" * (hostops.MAX_CERT_BYTES + 10))
        with pytest.raises(hostops.CertificateInvalidError):
            hostops.validate_public_certificate(big, "PEM")

    def test_rejects_pem_without_certificate_block(self):
        with pytest.raises(hostops.CertificateInvalidError):
            hostops.validate_public_certificate("just some text", "PEM")

    def test_rejects_garbage_that_claims_to_be_cert(self):
        fake = "-----BEGIN CERTIFICATE-----\nnot base64 valid!!\n-----END CERTIFICATE-----"
        with pytest.raises(hostops.CertificateInvalidError):
            hostops.validate_public_certificate(fake, "PEM")

    def test_rejects_invalid_der_base64(self):
        with pytest.raises(hostops.CertificateInvalidError):
            hostops.validate_public_certificate("!!!notbase64!!!", "DER")


# --------------------------------------------------------------------------- #
# Certificate staging CRUD
# --------------------------------------------------------------------------- #
@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REDFISH_SB_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path / "vars"))
    return tmp_path


class TestCertCrud:
    def test_add_then_get_and_list(self, state_dir):
        pem = _cert_pem("pk-cn")
        body, status = secureboot.add_cert(100, "PK", {"CertificateString": pem, "CertificateType": "PEM"})
        assert status == 201
        cid = body["Id"]
        assert body["CertificateString"] == pem
        # metadata enrichment present
        assert "ValidNotAfter" in body

        coll, st = secureboot.get_cert_collection(100, "PK")
        assert st == 200 and coll["Members@odata.count"] == 1

        got, st = secureboot.get_cert(100, "PK", cid)
        assert st == 200 and got["Id"] == cid

    def test_add_is_deduplicated_by_content(self, state_dir):
        pem = _cert_pem()
        b1, _ = secureboot.add_cert(100, "db", {"CertificateString": pem})
        b2, _ = secureboot.add_cert(100, "db", {"CertificateString": pem})
        assert b1["Id"] == b2["Id"]
        coll, _ = secureboot.get_cert_collection(100, "db")
        assert coll["Members@odata.count"] == 1

    def test_add_private_key_rejected(self, state_dir):
        body, status = secureboot.add_cert(100, "PK", {"CertificateString": _private_key_pem()})
        assert status == 400
        assert body["error"]["code"] == "Base.1.0.ActionParameterValueError"

    def test_add_missing_field(self, state_dir):
        body, status = secureboot.add_cert(100, "PK", {})
        assert status == 400

    def test_add_unknown_db(self, state_dir):
        body, status = secureboot.add_cert(100, "bogus", {"CertificateString": _cert_pem()})
        assert status == 404

    def test_delete(self, state_dir):
        pem = _cert_pem()
        body, _ = secureboot.add_cert(100, "KEK", {"CertificateString": pem})
        cid = body["Id"]
        _, status = secureboot.delete_cert(100, "KEK", cid)
        assert status == 204
        coll, _ = secureboot.get_cert_collection(100, "KEK")
        assert coll["Members@odata.count"] == 0

    def test_delete_missing(self, state_dir):
        _, status = secureboot.delete_cert(100, "KEK", "0123456789abcdef")
        assert status == 404

    def test_get_rejects_path_traversal_id(self, state_dir):
        # non-hex / traversal ids never resolve to a file
        body, status = secureboot.get_cert(100, "PK", "../../etc/passwd")
        assert status == 404
        assert secureboot._read_staged(100, "PK", "../../secret") is None


# --------------------------------------------------------------------------- #
# Dynamic varstore build
# --------------------------------------------------------------------------- #
class TestBuildVarstore:
    def test_tool_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        template = tmp_path / "OVMF_VARS_4M.blank.fd"
        template.write_bytes(b"\x00" * 1024)
        with patch("shutil.which", return_value=None):
            with pytest.raises(hostops.ToolMissingError):
                hostops.build_varstore_from_certs(
                    str(template), {"PK": [_cert_pem()]}, out_path=str(tmp_path / "out.fd")
                )

    def test_builds_argv_and_returns_sha(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path))
        template = tmp_path / "OVMF_VARS_4M.blank.fd"
        template.write_bytes(b"\x00" * 1024)
        out = tmp_path / "out.fd"

        def fake_run(argv, **kw):
            # virt-fw-vars "writes" the output
            out.write_bytes(b"BUILT")
            proc = MagicMock()
            proc.returncode = 0
            return proc

        with patch("shutil.which", return_value="/usr/bin/virt-fw-vars"), patch.object(
            hostops, "_run", side_effect=fake_run
        ) as run:
            sha = hostops.build_varstore_from_certs(
                str(template),
                {"PK": [_cert_pem("pk")], "db": [_cert_pem("db")]},
                out_path=str(out),
                secure_boot=True,
                no_microsoft=True,
            )
        argv = run.call_args[0][0]
        assert argv[0] == "virt-fw-vars"
        assert "--secure-boot" in argv and "--no-microsoft" in argv
        assert "--set-pk" in argv and "--add-db" in argv
        assert sha == hostops._sha256_file(str(out))

    def test_rejects_template_outside_allowlist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDFISH_SB_VARSTORE_DIR", str(tmp_path / "vars"))
        (tmp_path / "vars").mkdir()
        outside = tmp_path / "evil.fd"
        outside.write_bytes(b"x")
        with patch("shutil.which", return_value="/usr/bin/virt-fw-vars"):
            with pytest.raises(hostops.SourceNotAllowedError):
                hostops.build_varstore_from_certs(
                    str(outside), {"PK": [_cert_pem()]}, out_path=str(tmp_path / "vars" / "o.fd")
                )


@contextmanager
def _fake_guard(proxmox, vmid, allow_autostop=False):
    yield False


def _write_result():
    return hostops.WriteResult(
        wrote=True,
        verified=True,
        image_path="/x",
        image_sha256="sha",
        device_path="/dev/pve/vm-100-disk-0",
        bytes_considered=1024,
        dry_run=False,
        message="ok",
    )


class TestApplyStagedCerts:
    def test_patch_enable_uses_staged_certs(self, state_dir, monkeypatch):
        # stage a PK cert
        secureboot.add_cert(100, "PK", {"CertificateString": _cert_pem()})
        with patch.object(secureboot.hostops, "build_varstore_from_certs", return_value="sha") as build, patch.object(
            secureboot.hostops, "stopped_vm_guard", _fake_guard
        ), patch.object(secureboot.hostops, "locate_efidisk", return_value=MagicMock()), patch.object(
            secureboot.hostops, "write_varstore_image", return_value=_write_result()
        ):
            body, status = secureboot.patch_secureboot(MagicMock(), 100, {"SecureBootEnable": True})
        assert status == 200
        build.assert_called_once()
        state = secureboot.read_state(100)
        assert state["profile"] == "dynamic-certs"
        assert state["has_pk"] is True
