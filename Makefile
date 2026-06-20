VERSION ?= 0.2.5

.PHONY: deb deb-dpkg clean test help

help:
	@echo "make deb        - build dist/proxmox-redfish_$(VERSION)_all.deb (pure Python, no dpkg needed)"
	@echo "make deb-dpkg   - build with dpkg-deb instead (Debian hosts)"
	@echo "make test       - run the unit test suite"
	@echo "make clean      - remove build artifacts"

# Default: portable builder that writes the .deb directly (works on macOS).
deb:
	python3 packaging/build_deb.py $(VERSION)
	@echo "Copy dist/proxmox-redfish_$(VERSION)_all.deb to the Proxmox host, then:"
	@echo "  sudo apt install ./proxmox-redfish_$(VERSION)_all.deb     # install"
	@echo "  sudo apt purge proxmox-redfish                            # remove every trace"

# Alternative: native dpkg-deb (only on hosts that have it).
deb-dpkg:
	@command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb not found; use 'make deb'"; exit 1; }
	python3 packaging/build_deb.py $(VERSION)

test:
	REDFISH_LOGGING_ENABLED=false PYTHONPATH=src python3 -m pytest tests/unit -q

clean:
	rm -rf dist
