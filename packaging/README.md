# Debian packaging

Build a `.deb` for Proxmox VE (Debian) hosts.

## Build

```bash
make deb            # -> dist/proxmox-redfish_<version>_all.deb  (works on macOS; no dpkg needed)
make deb VERSION=0.3.0
```

`make deb` runs `packaging/build_deb.py`, which stages the tree, vendors the two
pure-Python runtime deps (`proxmoxer`, `requests-toolbelt`) as offline wheels, and
writes the `.deb` ar archive directly — so it builds on macOS or Linux with just
Python + pip. (`make deb-dpkg` uses `dpkg-deb` instead, on hosts that have it.)

## Install (on the Proxmox host)

```bash
sudo apt install ./proxmox-redfish_0.2.0_all.deb
sudo editor /etc/proxmox-redfish/params.env      # set PROXMOX_HOST / USER / PASSWORD
sudo systemctl start proxmox-redfish
# Endpoint: https://<host>:8443/redfish/v1
```

What the package does on install (`postinst`):
- builds an isolated venv at `/opt/proxmox-redfish/venv` (`--system-site-packages`, so
  it uses the apt `python3-requests`/`python3-cryptography`) and adds `proxmoxer` +
  `requests-toolbelt` from the bundled offline wheels (no network required);
- creates `/var/lib/proxmox-redfish/{secureboot,varstores}`;
- generates a self-signed TLS cert in `/etc/proxmox-redfish/`;
- enables (but does not start) the systemd service.

Runs as **root** — SecureBoot enrollment writes the VM efidisk LVM volume.

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
| `/opt/proxmox-redfish/venv/` | runtime venv (created at install) |
| `/opt/proxmox-redfish/wheels/` | bundled offline wheels |
| `/etc/proxmox-redfish/params.env` | configuration (conffile) |
| `/etc/proxmox-redfish/server.{crt,key}` | TLS cert (generated) |
| `/var/lib/proxmox-redfish/` | SecureBoot state + varstores |
| `/lib/systemd/system/proxmox-redfish.service` | service unit |
