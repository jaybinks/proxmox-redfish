# Debian packaging

Build a `.deb` for Proxmox VE (Debian) hosts.

## Build

```bash
make deb            # -> dist/proxmox-redfish_<version>_all.deb  (works on macOS; no dpkg needed)
make deb VERSION=0.3.0
```

`make deb` runs `packaging/build_deb.py`, which stages the tree, vendors the two
pure-Python runtime deps (`proxmoxer`, `requests-toolbelt`) **unpacked** under
`vendor/`, and writes the `.deb` ar archive directly — so it builds on macOS or Linux
with just Python + pip. (`make deb-dpkg` uses `dpkg-deb` instead, on hosts that have it.)

## Install (on the Proxmox host)

```bash
sudo apt install ./proxmox-redfish_0.2.1_all.deb
sudo editor /etc/proxmox-redfish/params.env      # set PROXMOX_HOST / USER / PASSWORD
sudo systemctl start proxmox-redfish
# Endpoint: https://<host>:8443/redfish/v1
```

**No venv, no pip, no network.** The app runs on the system `python3` (Proxmox VE ships
3.11) with the apt-provided `python3-requests` / `python3-cryptography`, plus the two
pure-Python deps (`proxmoxer`, `requests-toolbelt`) bundled **unpacked** under
`/opt/proxmox-redfish/vendor` and put on `PYTHONPATH` by the systemd unit.

What the package does on install (`postinst`):
- creates `/var/lib/proxmox-redfish/{secureboot,varstores}`;
- generates a self-signed TLS cert in `/etc/proxmox-redfish/`;
- import-checks the app on the system interpreter;
- enables (but does not start) the systemd service.

Dependencies (all present on a current Proxmox VE host): `python3-requests`,
`python3-cryptography`; recommends `python3-virt-firmware` (for SecureBoot dynamic
varstore build). Runs as **root** — SecureBoot enrollment writes the VM efidisk LVM volume.

## TLS / certificates

TLS is **optional** and, when enabled, **reuses the Proxmox certificate** by default —
the Redfish endpoint presents the same identity as the Proxmox API on `:8006`.

Certificate resolution (in order), controlled by `/etc/proxmox-redfish/params.env`:

1. `REDFISH_USE_TLS="false"` → serve **plain HTTP** (e.g. behind a reverse proxy, or if
   you simply don't want TLS). No cert needed.
2. `SSL_CERT_FILE` + `SSL_KEY_FILE` set and present → use those (bring your own cert).
3. `/etc/pve/local/pveproxy-ssl.{pem,key}` → the **custom** cert an admin uploaded to
   Proxmox (if any) — identical to what `:8006` serves.
4. `/etc/pve/local/pve-ssl.{pem,key}` → the **Proxmox node cert** (always present on a
   PVE host). **This is the default** — same self-signed-by-the-PVE-CA identity as the API.

So on a stock Proxmox host you get TLS using the node cert automatically, with nothing to
configure. If no cert is resolvable (e.g. a non-PVE test box), the daemon logs a warning
and falls back to plain HTTP rather than failing.

The daemon runs as root and can read `/etc/pve/local/`. To use a fully custom cert, point
`SSL_CERT_FILE`/`SSL_KEY_FILE` at it. To turn TLS off entirely, set `REDFISH_USE_TLS="false"`.

## Remove

```bash
sudo apt remove proxmox-redfish     # stops service, removes app + venv
sudo apt purge  proxmox-redfish     # removes EVERY trace: /opt, /var/lib, /etc, unit
```

`purge` deletes `/opt/proxmox-redfish`, `/var/lib/proxmox-redfish`,
`/etc/proxmox-redfish`, and the systemd unit — nothing is left behind. The only
config marked as a conffile is `/etc/proxmox-redfish/params.env`; everything else the
package creates is cleaned by `postrm`.

## Layout installed

| Path | Contents |
|------|----------|
| `/opt/proxmox-redfish/src/` | application modules |
| `/opt/proxmox-redfish/vendor/` | bundled pure-Python deps (proxmoxer, requests-toolbelt) |
| `/etc/proxmox-redfish/params.env` | configuration (conffile) |
| `/etc/proxmox-redfish/server.{crt,key}` | TLS cert (generated) |
| `/var/lib/proxmox-redfish/` | SecureBoot state + varstores |
| `/lib/systemd/system/proxmox-redfish.service` | service unit |
