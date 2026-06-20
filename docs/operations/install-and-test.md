# Install & test — SecureBoot on Proxmox

Phase 1 (static varstore-swap) install + validation runbook. The daemon runs **on
the Proxmox host as root** (required to `dd` the efidisk LV). Writes are **dry-run
by default** — you opt in explicitly after dry-run looks correct.

## 1. Install the daemon on the PVE host

```bash
git clone https://github.com/jaybinks/proxmox-redfish.git /opt/proxmox-redfish
cd /opt/proxmox-redfish
python3 -m venv venv && . venv/bin/activate
pip install -e .
# TLS cert (self-signed example)
openssl req -x509 -newkey rsa:4096 -keyout config/ssl/server.key \
  -out config/ssl/server.crt -days 365 -nodes -subj "/CN=$(hostname)"
```

## 2. Stage the varstore image(s) and profile catalog

```bash
mkdir -p /opt/proxmox-redfish/varstores /var/lib/proxmox-redfish/secureboot
# Copy your pre-baked image (the one you currently dd by hand) into the allowlisted dir:
cp /var/lib/vz/template/iso/ngv-ovmf-vars-3009.img /opt/proxmox-redfish/varstores/ngv-ovmf-vars.img
# Optional blank/setup-mode varstore for SecureBootEnable=false / DeleteAllKeys:
cp /usr/share/OVMF/OVMF_VARS_4M.fd /opt/proxmox-redfish/varstores/OVMF_VARS_4M.blank.fd

cp config/secureboot_profiles.json.example /opt/proxmox-redfish/config/secureboot_profiles.json
# Fill in the real sha256 so the daemon verifies the image before every write (INV-11):
sha256sum /opt/proxmox-redfish/varstores/*.img /opt/proxmox-redfish/varstores/*.fd
# edit /opt/proxmox-redfish/config/secureboot_profiles.json -> image_sha256 fields
```

> The image content is the same NGV key set for any VM, so one shared
> `ngv-ovmf-vars.img` works across VMs. (If you truly need per-VM images, point the
> profile `image_path` at the specific file — it must stay inside the allowlisted dir.)

## 3. Configure env

```bash
cp config/params.env.example config/params.env
# edit PROXMOX_HOST/USER/PASSWORD/NODE, and the REDFISH_SB_* block.
# Leave REDFISH_SB_ALLOW_WRITE=0 for the first run (dry-run).
```

## 4. Run

```bash
set -a; . config/params.env; set +a
python src/proxmox_redfish/proxmox_redfish.py --port 8443    # or use the systemd unit
```

## 5. Validate (dry-run first)

```bash
BASE=https://localhost:8443 ; U=$REDFISH_USER ; P=$REDFISH_PASS ; VMID=3009

# Read current state (also confirms the VM has a 4m efidisk):
curl -ks -u "$U:$P" "$BASE/redfish/v1/Systems/$VMID/SecureBoot" | jq

# Stop the VM (or set REDFISH_SB_ALLOW_AUTOSTOP=1 to let the daemon do it):
qm stop $VMID

# Dry-run enable (REDFISH_SB_ALLOW_WRITE=0): all safety checks run, NO write.
curl -ks -u "$U:$P" -X PATCH -H 'Content-Type: application/json' \
  -d '{"SecureBootEnable": true}' \
  "$BASE/redfish/v1/Systems/$VMID/SecureBoot" | jq '.Oem.Proxmox'
# Expect: "DryRun": true, "LastOperation": "dry-run: all safety checks passed; ..."
```

Check the audit log (syslog/journal) for the exact `dd` argv that *would* run:

```bash
journalctl -t proxmox-redfish | grep 'AUDIT sb' | tail
```

Confirm the resolved device is the right `/dev/pve/vm-3009-disk-N`, the image sha256
matches, and sizes line up.

## 6. Go live

```bash
# Only after the dry-run argv/device/sha look correct:
export REDFISH_SB_ALLOW_WRITE=1   # (set in params.env and restart the daemon)

qm stop $VMID
curl -ks -u "$U:$P" -X PATCH -H 'Content-Type: application/json' \
  -d '{"SecureBootEnable": true}' \
  "$BASE/redfish/v1/Systems/$VMID/SecureBoot" | jq
qm start $VMID
```

The VM now boots enforcing Secure Boot under your NGV keys — the same end state as
the manual `dd`, driven through the Redfish standard. The standard
`SecureBoot.ResetKeys` action works too:

```bash
curl -ks -u "$U:$P" -X POST -H 'Content-Type: application/json' \
  -d '{"ResetKeysType": "ResetAllKeysToDefault"}' \
  "$BASE/redfish/v1/Systems/$VMID/SecureBoot/Actions/SecureBoot.ResetKeys" | jq
```

## Safety recap

- Writes are refused unless the VM is **stopped** (INV-08/09), the device path is
  **derived from Proxmox config** and matches `/dev/pve/vm-<vmid>-disk-N` (INV-02/05/07),
  the efidisk is **efitype=4m** (INV-04), the image is inside the **allowlisted dir**
  with a **matching sha256** (INV-10/11), and the image is **not larger** than the LV
  (INV-12). Every write is **verified by read-back** (INV-18) and **audit-logged**
  (INV-17). See [../SECURITY.md](../SECURITY.md).
- If anything is ambiguous, the daemon **fails closed** and writes nothing.

## Tests (dev host, no Proxmox needed)

```bash
REDFISH_LOGGING_ENABLED=false PYTHONPATH=src \
  python -m pytest tests/unit/test_hostops.py tests/unit/test_secureboot.py -q
```
