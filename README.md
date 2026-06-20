# Proxmox Redfish Daemon

[![DMTF Service-Validator](https://img.shields.io/badge/Service--Validator-639%20PASS%20%2F%200%20FAIL-brightgreen.svg)](RedFishSpecCompliance.md)
[![DMTF Protocol-Validator](https://img.shields.io/badge/Protocol--Validator-287%20PASS%20%2F%200%20FAIL-brightgreen.svg)](RedFishSpecCompliance.md)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![Packaging](https://img.shields.io/badge/install-.deb-blue.svg)](packaging/README.md)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

A **DMTF Redfish API daemon for Proxmox VE** that exposes each VM as a Redfish-managed
"bare-metal" node: power control, virtual media, boot-device override, UEFI/BIOS firmware
mode, and full **UEFI Secure Boot key management**. It lets Proxmox VMs be driven by the
same tooling used for physical servers — **Metal3, OpenStack Ironic, sushy, and OpenShift
ZTP / ACM GitOps** — through the standard Redfish protocol.

> This is a substantially extended fork of [`v1k0d3n/proxmox-redfish`](https://github.com/v1k0d3n/proxmox-redfish)
> by Brandon B. Jozsa. It builds on that foundation to reach **full DMTF spec coverage**,
> adds **Secure Boot enrollment**, **Debian packaging**, and **remote logging**, and now
> passes both official DMTF validators with **0 FAIL**. See [Acknowledgements](#acknowledgements).

## Highlights

- ✅ **Passes the DMTF Redfish-Service-Validator** (schema/CSDL conformance): **639 PASS / 0 FAIL** over HTTPS.
- ✅ **Passes the DMTF Redfish-Protocol-Validator** (DSP0266 HTTP behaviour): **287 PASS / 0 FAIL** over HTTPS.
- 🤝 **Cross-client compatible** — verified against OpenStack **sushy**, DMTF **python-redfish-library**, and DMTF **redfishtool**.
- 🔐 **UEFI Secure Boot enrollment** — enroll custom PK/KEK/db keys via Redfish, applied to a host-side OVMF varstore.
- 📦 **Ships as a `.deb`** — depends only on packages already present on a Proxmox VE host; no venv, no pip, no network.
- 📡 **Remote logging to Grafana Loki** — OS-independent structured log push.
- 🔁 **Full Redfish surface** — Systems, Chassis, Managers, Tasks, Sessions, Accounts, Events, Update, and Certificate services.

## Table of Contents

- [Install — Debian package (recommended)](#install--debian-package-recommended)
- [Install — from source (development)](#install--from-source-development)
- [Configuration](#configuration)
- [Redfish API coverage](#redfish-api-coverage)
- [Secure Boot](#secure-boot)
- [Remote logging (Grafana Loki)](#remote-logging-grafana-loki)
- [Documentation](#documentation)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## Install — Debian package (recommended)

The primary, supported install method is a Debian package. It runs on the system
`python3` that Proxmox VE already ships (3.11), using the apt-provided
`python3-requests` and `python3-cryptography` — **no virtualenv, no pip, no network access
required on the host**.

**1. Build the `.deb`** (on your workstation — macOS or Linux, no `dpkg` needed):

```bash
make deb                  # -> dist/proxmox-redfish_<version>_all.deb
make deb VERSION=0.3.0    # build a specific version
```

`make deb` runs `packaging/build_deb.py`, which stages the tree, vendors the two
pure-Python runtime deps (`proxmoxer`, `requests-toolbelt`) unpacked under `vendor/`,
and writes the `.deb` archive directly. (`make deb-dpkg` uses `dpkg-deb` on hosts that
have it.)

**2. Copy it to the Proxmox host and install:**

```bash
sudo apt install ./proxmox-redfish_<version>_all.deb
# or:  sudo dpkg -i proxmox-redfish_<version>_all.deb
```

On install (`postinst`) the package creates `/var/lib/proxmox-redfish/{secureboot,varstores}`,
generates a self-signed TLS cert in `/etc/proxmox-redfish/`, import-checks the app on the
system interpreter, and enables (but does not start) the systemd service.

**3. Configure and start:**

```bash
sudo editor /etc/proxmox-redfish/params.env   # set PROXMOX_HOST / USER / PASSWORD / NODE
sudo systemctl start proxmox-redfish
```

The endpoint is then available at:

```
https://<host>:8443/redfish/v1
```

**TLS notes.** TLS is **optional** and, when enabled (the default), **auto-reuses the
Proxmox node certificate** (`/etc/pve/local/pve-ssl.{pem,key}`) — so the Redfish endpoint
presents the *same identity* as the Proxmox API on `:8006`, with nothing to configure on a
stock PVE host. You can instead bring your own cert (`SSL_CERT_FILE`/`SSL_KEY_FILE`), or
disable TLS entirely with `REDFISH_USE_TLS="false"` to serve plain HTTP (e.g. behind a
reverse proxy).

**Runs as root** — Secure Boot enrollment writes the VM's efidisk LVM volume.

**Clean removal:**

```bash
sudo apt remove proxmox-redfish    # stops the service, removes the app
sudo apt purge  proxmox-redfish    # removes EVERY trace: /opt, /var/lib, /etc, unit
```

See [`packaging/README.md`](packaging/README.md) for the full installed layout, cert
resolution order, and build internals.

## Install — from source (development)

For development and testing:

```bash
git clone https://github.com/v1k0d3n/proxmox-redfish.git
cd proxmox-redfish
python3 -m venv venv && source venv/bin/activate
pip install -e .
```

Then create a `config/params.env` from [`config/params.env.example`](config/params.env.example)
and run the daemon directly. See the [Contributor Guide](docs/contrib/README.md) for the
test suite and validator harness.

## Configuration

Configuration lives in `/etc/proxmox-redfish/params.env` (package install) or
`config/params.env` (source install). Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PROXMOX_HOST` | `127.0.0.1` | Proxmox API host/IP. |
| `PROXMOX_USER` | `root@pam` | Proxmox API user (fallback). |
| `PROXMOX_PASSWORD` | `CHANGE_ME` | Proxmox API password (fallback). |
| `PROXMOX_NODE` | `pve` | Proxmox node name. |
| `PROXMOX_API_PORT` | `8006` | Proxmox API port. |
| `VERIFY_SSL` | `false` | Verify TLS when talking *to* the Proxmox API. |
| `REDFISH_USE_TLS` | `true` | Serve the Redfish endpoint over TLS (`false` = plain HTTP). |
| `SSL_CERT_FILE` / `SSL_KEY_FILE` | *(empty)* | Bring-your-own cert; empty = auto-resolve the Proxmox node cert. |
| `REDFISH_STRICT_PROTOCOL` | `0` | `0` = lenient (max client compatibility); `1` = strict DSP0266 (validator runs). |
| `REDFISH_SB_ALLOW_WRITE` | `0` | Secure Boot writes are **dry-run** until set to `1`. |
| `REDFISH_SB_PROFILES` | `…/secureboot_profiles.json` | Secure Boot enrollment profiles. |
| `REDFISH_SB_VARSTORE_DIR` | `/var/lib/proxmox-redfish/varstores` | OVMF varstore working dir. |
| `REDFISH_SB_VG_ALLOWLIST` | `pve` | LVM volume groups Secure Boot writes may touch. |
| `REDFISH_AUTO_EFIDISK` | `1` | Auto-provision a 4 MB OVMF efidisk on `FirmwareMode=UEFI`. |
| `REDFISH_LOKI_URL` | *(empty)* | Grafana Loki push URL (empty disables remote logging). |
| `REDFISH_LOKI_LABELS` | `job=proxmox-redfish` | Loki stream labels. |
| `REDFISH_LOKI_USER` / `REDFISH_LOKI_PASSWORD` / `REDFISH_LOKI_TENANT` | *(empty)* | Loki auth / multi-tenancy. |
| `REDFISH_LOG_LEVEL` | `INFO` | Local log level (`DEBUG` for verbose). |

> **Credential pass-through.** A Redfish client authenticates to this daemon with HTTP
> **Basic auth** (or a Redfish session). Those caller credentials are passed *through* to
> Proxmox for the operation, so each Redfish user maps to a real Proxmox identity. The
> `PROXMOX_USER`/`PROXMOX_PASSWORD` in `params.env` are a fallback. Use a
> [least-privilege Proxmox service account](docs/admins/README.md) in production.

### Authenticating with a Proxmox API token (recommended)

Tokens are revocable and avoid putting a password on the wire. Create one on the Proxmox host:

```bash
# Full-access token (inherits the user's privileges):
pveum user token add root@pam redfish --privsep 0 -comment "redfish"
#  -> prints the token id (root@pam!redfish) and a one-time secret (a UUID)

# Or least-privilege, read-only on one VM:
pveum user token add svc@pve readonly --privsep 1
pveum acl modify /vms/4000 -token 'svc@pve!readonly' -role PVEAuditor
```

Pass the token as Basic auth — **username = the token id**, **password = the secret**:

```bash
TOKEN='root@pam!redfish'
SECRET='xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'

curl -k -u "$TOKEN:$SECRET" https://<host>:8443/redfish/v1/Systems | jq
curl -k -u "$TOKEN:$SECRET" https://<host>:8443/redfish/v1/Systems/4000 | jq
```

Client libraries take the same id/secret as username/password:

```python
# OpenStack sushy
import sushy
conn = sushy.Sushy("https://<host>:8443/redfish/v1",
                   username="root@pam!redfish", password=SECRET, verify=False)

# DMTF python-redfish-library
import redfish
c = redfish.redfish_client("https://<host>:8443",
                           username="root@pam!redfish", password=SECRET)
c.login(auth="basic")
```

```bash
# DMTF redfishtool
redfishtool -r <host>:8443 -S Always -u 'root@pam!redfish' -p "$SECRET" Systems list
```

Roles needed: **read** operations → `PVEAuditor`; **power / boot / virtual media** →
`PVEVMAdmin` (or `VM.PowerMgmt` + `VM.Config.*`); **Secure Boot enrollment** runs host-side as
root. Revoke a token with `pveum user token remove root@pam redfish`.

## Redfish API coverage

The daemon implements the full provisioning-critical Redfish surface plus the surrounding
service tree needed to crawl clean against the validators.

| Resource / Service | Methods | Status |
|--------------------|---------|--------|
| `ServiceRoot` (`/redfish/v1`) | GET | ✅ |
| `SessionService` + `Sessions` (login / list / **logout**) | GET, POST, DELETE | ✅ |
| `Systems` + `Systems/{id}` (inventory, power) | GET, PATCH | ✅ |
| `…/Actions/ComputerSystem.Reset` (power control) | POST | ✅ |
| Boot override (`BootSourceOverride*`) | PATCH | ✅ |
| `…/Bios` (firmware mode: SeaBIOS / OVMF-UEFI) | GET, PATCH | ✅ |
| `…/Processors`, `…/Storage`, `…/Memory`, `…/EthernetInterfaces` | GET | ✅ |
| `…/LogServices` | GET | ✅ |
| `…/SecureBoot` (+ `SecureBootDatabases`, `Certificates`, `ResetKeys`) | GET, PATCH, POST, DELETE | ✅ |
| `Chassis` + per-VM member (`ChassisType: VirtualMachine`) | GET | ✅ |
| `Managers/{id}` + `VirtualMedia/Cd` (insert / eject) | GET, POST | ✅ |
| `TaskService` + `Tasks/{upid}` (Proxmox UPID → Redfish Task) | GET | ✅ |
| `AccountService` + Accounts / Roles (Proxmox users + roles) | GET | ✅ |
| `EventService` + Subscriptions | GET, POST, DELETE | ✅ |
| `UpdateService` (reported `State: Absent` — VMs have no firmware surface) | GET | ✅ |
| `CertificateService` | GET | ✅ |

> Status reflects schema-clean, validator-crawled resources. Some inventory resources are
> read-only and some (e.g. Chassis Power/Thermal) are synthetic because VMs have no physical
> sensors — these are honestly marked `Oem.Proxmox.Synthetic`. See
> [`RedFishSpecCompliance.md`](RedFishSpecCompliance.md) for the property-level matrix,
> documented variances, and VM-specific exceptions.

**Validated by** — both official DMTF conformance tools, run over HTTPS, report **0 FAIL**:

```
Redfish-Service-Validator  (schema / CSDL):  PASS 639 | FAIL 0
Redfish-Protocol-Validator (DSP0266 / HTTP): PASS 287 | FAIL 0
```

**Cross-client compatibility** — exercised in CI against three independent Redfish clients:

| Client | Result |
|--------|--------|
| OpenStack **sushy** (Ironic) | Connect, parse System / Boot / SecureBoot, drive `ComputerSystem.Reset` ✅ |
| DMTF **python-redfish-library** (`redfish`) | Session login, GET ServiceRoot / Systems / SecureBoot ✅ |
| DMTF **redfishtool** (CLI) | `Systems list` ✅ |

The server runs in **lenient** protocol mode by default for maximum real-world client
compatibility; set `REDFISH_STRICT_PROTOCOL=1` to enforce full DSP0266 (as used for the
validator runs).

## Secure Boot

This fork adds first-class **UEFI Secure Boot key management** through the standard Redfish
`SecureBoot` model:

- Enroll custom **PK / KEK / db** keys either via predefined **profiles**
  (`SecureBoot.ResetKeys` → `ResetAllKeysToDefault` / `DeleteAllKeys` / `DeletePK`) or via
  **certificate CRUD** on `SecureBootDatabases/{db}/Certificates` (PEM/DER public certs;
  private keys are rejected).
- On `PATCH SecureBoot {SecureBootEnable: true}`, the daemon builds an OVMF varstore from
  the staged certificates (`virt-fw-vars`) and writes it to the VM's host-side efidisk
  LVM volume.
- Switching a VM to UEFI auto-provisions a 4 MB OVMF efidisk if one is absent, so Secure
  Boot has persistent NVRAM to target.
- **Writes are dry-run by default** (`REDFISH_SB_ALLOW_WRITE=0`); they are guarded by an
  LVM volume-group allowlist and the `INV-*` safety checks documented in
  [`docs/SECURITY.md`](docs/SECURITY.md).

Design rationale is in
[`docs/decisions/0001-secureboot-via-varstore-swap.md`](docs/decisions/0001-secureboot-via-varstore-swap.md).

## Remote logging (Grafana Loki)

The daemon can push structured logs directly to **Grafana Loki**, independent of the host
OS or journald. Set `REDFISH_LOKI_URL` to your Loki push endpoint (an empty URL disables
it). Stream labels, basic-auth credentials, and multi-tenant headers are configurable via
`REDFISH_LOKI_LABELS`, `REDFISH_LOKI_USER`/`REDFISH_LOKI_PASSWORD`, and
`REDFISH_LOKI_TENANT`. Local logging continues to `journalctl -u proxmox-redfish`.

## Documentation

| Document | Contents |
|----------|----------|
| [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) | Functional + non-functional requirements (with IDs). |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Daemon structure and where Secure Boot fits. |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model + the `INV-*` guard rails for host-side writes. |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phased delivery and acceptance criteria. |
| [`docs/PARITY-PLAN.md`](docs/PARITY-PLAN.md) | Plan to full DMTF parity, with VM-specific exceptions. |
| [`RedFishSpecCompliance.md`](RedFishSpecCompliance.md) | Coverage tables, validator results, cross-client matrix, variances. |
| [`docs/research/redfish-validation-tools.md`](docs/research/redfish-validation-tools.md) | DMTF conformance validators research (Service / Protocol / Interop / Usecase). |
| [`packaging/README.md`](packaging/README.md) | Debian package build, install, and layout details. |
| [`docs/README.md`](docs/README.md) | Full documentation index and reading order. |

Audience guides: [User Guide](docs/users/README.md) · [Admin Guide](docs/admins/README.md) · [Contributor Guide](docs/contrib/README.md).

## Acknowledgements

This project stands on the shoulders of the original
**[`v1k0d3n/proxmox-redfish`](https://github.com/v1k0d3n/proxmox-redfish)** by
**Brandon B. Jozsa** ([@v1k0d3n](https://github.com/v1k0d3n)). That implementation framed
the idea of fronting Proxmox VMs with a Redfish daemon and built the foundation this work
depends on — the core daemon, the provisioning workflow, and the integration story with
Metal3 / Ironic / ZTP. **Sincere thanks to Brandon**: this fork would not exist without it.
What we have added on top — full DMTF spec coverage, UEFI Secure Boot enrollment, Debian
packaging, Grafana Loki logging, and 0-FAIL conformance on both validators — is an
extension of his original vision, not a replacement for it.

With gratitude also to:

- **[DMTF](https://www.dmtf.org/standards/redfish)** — for the Redfish standard (DSP0266 /
  DSP2046) and the open-source **Redfish-Service-Validator** and **Redfish-Protocol-Validator**
  that this project is held to.
- **[OpenStack sushy](https://opendev.org/openstack/sushy)**, **[DMTF redfishtool](https://github.com/DMTF/Redfishtool)**,
  and **[DMTF python-redfish-library](https://github.com/DMTF/python-redfish-library)** — the
  client projects that define real-world interoperability and against which this daemon is tested.

## License

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE) for details.
