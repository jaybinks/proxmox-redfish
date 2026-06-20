#!/usr/bin/env python3
"""
Build a proxmox-redfish .deb without dpkg-deb (works on macOS).

A Debian package is an `ar` archive of three members, in order:
    debian-binary, control.tar.gz, data.tar.gz
This script stages the filesystem tree, vendors the two pure-Python runtime deps
as offline wheels, builds both tarballs, computes md5sums, and writes the ar
archive by hand -- so it runs anywhere Python + pip exist.

Usage: python3 packaging/build_deb.py [VERSION]   (default version from env or 0.2.0)
"""

import gzip
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "packaging")
DIST = os.path.join(ROOT, "dist")


def _tarinfo(name, size, mode, mtime, is_dir=False):
    ti = tarfile.TarInfo(name)
    ti.size = size
    ti.mode = mode
    ti.mtime = mtime
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = "root"
    ti.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    return ti


def _add_tree(tar, src_dir, arc_prefix, mtime):
    """Add a directory tree to the tar under arc_prefix (e.g. './opt/...')."""
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames.sort()
        rel = os.path.relpath(dirpath, src_dir)
        arc_dir = arc_prefix if rel == "." else os.path.join(arc_prefix, rel)
        tar.addfile(_tarinfo(arc_dir + "/", 0, 0o755, mtime, is_dir=True))
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            mode = 0o755 if os.access(full, os.X_OK) else 0o644
            with open(full, "rb") as fh:
                data = fh.read()
            tar.addfile(_tarinfo(os.path.join(arc_dir, fn), len(data), mode, mtime), io.BytesIO(data))


def _gz_tar(build_fn):
    """Run build_fn(tar) into a gzipped tar, return bytes (deterministic mtime)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        build_fn(tar)
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as g:
        g.write(raw.getvalue())
    return gz.getvalue()


def _ar_member(name, data, mtime):
    header = "{:<16}{:<12}{:<6}{:<6}{:<8}{:<10}`\n".format(name, mtime, 0, 0, 100644, len(data))
    out = header.encode("ascii") + data
    if len(data) % 2:  # ar members are padded to even length
        out += b"\n"
    return out


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else os.getenv("VERSION", "0.2.0")
    mtime = int(os.getenv("SOURCE_DATE_EPOCH", "1700000000"))
    stage = os.path.join(DIST, "pkgroot")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    os.makedirs(DIST, exist_ok=True)

    app = os.path.join(stage, "opt/proxmox-redfish")
    # --- application source ---
    os.makedirs(os.path.join(app, "src/proxmox_redfish"))
    for fn in os.listdir(os.path.join(ROOT, "src/proxmox_redfish")):
        if fn.endswith(".py"):
            shutil.copy2(os.path.join(ROOT, "src/proxmox_redfish", fn), os.path.join(app, "src/proxmox_redfish", fn))
    for fn in ("setup.py", "pyproject.toml", "requirements.txt"):
        shutil.copy2(os.path.join(ROOT, fn), os.path.join(app, fn))
    os.makedirs(os.path.join(app, "config"))
    for fn in os.listdir(os.path.join(ROOT, "config")):
        if fn.endswith(".example"):
            shutil.copy2(os.path.join(ROOT, "config", fn), os.path.join(app, "config", fn))

    # --- vendored pure-Python deps, UNPACKED (no venv/pip needed at install) ---
    vendor = os.path.join(app, "vendor")
    os.makedirs(vendor)
    print("Vendoring pure-Python deps unpacked (proxmoxer, requests-toolbelt)...")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-compile",
            "--target",
            vendor,
            "proxmoxer",
            "requests-toolbelt",
        ]
    )
    # Drop pip's bookkeeping dirs to keep the package tidy.
    for junk in os.listdir(vendor):
        if junk.endswith(".dist-info") or junk == "__pycache__":
            shutil.rmtree(os.path.join(vendor, junk), ignore_errors=True)

    # --- systemd unit + config ---
    os.makedirs(os.path.join(stage, "lib/systemd/system"))
    shutil.copy2(os.path.join(PKG, "proxmox-redfish.service"), os.path.join(stage, "lib/systemd/system/proxmox-redfish.service"))
    os.makedirs(os.path.join(stage, "etc/proxmox-redfish"))
    shutil.copy2(os.path.join(PKG, "params.env"), os.path.join(stage, "etc/proxmox-redfish/params.env"))

    # --- data.tar.gz ---
    def build_data(tar):
        _add_tree(tar, stage, ".", mtime)

    data_gz = _gz_tar(build_data)

    # --- md5sums (relative paths, no leading ./) ---
    md5_lines = []
    for dirpath, _, filenames in os.walk(stage):
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, stage)
            with open(full, "rb") as fh:
                md5_lines.append("{}  {}".format(hashlib.md5(fh.read()).hexdigest(), rel))
    md5sums = ("\n".join(sorted(md5_lines)) + "\n").encode()

    # --- control.tar.gz ---
    with open(os.path.join(PKG, "control")) as fh:
        control = fh.read().replace("@VERSION@", version)
    # installed-size in KiB
    total = sum(
        os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(stage) for f in fs
    )
    control += "Installed-Size: {}\n".format(max(1, total // 1024))

    def build_control(tar):
        tar.addfile(_tarinfo("./", 0, 0o755, mtime, is_dir=True))
        members = {"control": (control.encode(), 0o644)}
        with open(os.path.join(PKG, "conffiles")) as fh:
            members["conffiles"] = (fh.read().encode(), 0o644)
        members["md5sums"] = (md5sums, 0o644)
        for script in ("postinst", "prerm", "postrm"):
            with open(os.path.join(PKG, script)) as fh:
                members[script] = (fh.read().encode(), 0o755)
        for name, (blob, mode) in members.items():
            tar.addfile(_tarinfo("./" + name, len(blob), mode, mtime), io.BytesIO(blob))

    control_gz = _gz_tar(build_control)

    # --- assemble the ar archive ---
    out = os.path.join(DIST, "proxmox-redfish_{}_all.deb".format(version))
    with open(out, "wb") as fh:
        fh.write(b"!<arch>\n")
        fh.write(_ar_member("debian-binary", b"2.0\n", mtime))
        fh.write(_ar_member("control.tar.gz", control_gz, mtime))
        fh.write(_ar_member("data.tar.gz", data_gz, mtime))

    print("Built {} ({} KiB)".format(out, os.path.getsize(out) // 1024))


if __name__ == "__main__":
    main()
