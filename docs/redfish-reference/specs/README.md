# Redfish spec documents (not mirrored — links only)

The DSP specification documents are large, versioned PDFs/HTML. We do **not** mirror them here;
fetch from the authoritative URLs in `../REFERENCES.md`. The machine-readable JSON Schemas they
define **are** mirrored in `../schemas/` (that is what code and tests validate against).

| Doc | Covers | URL |
|-----|--------|-----|
| DSP0266 | Protocol: HTTP methods, status codes, ETag/If-Match, async Tasks, sessions/auth, error envelope | https://www.dmtf.org/dsp/DSP0266 |
| DSP2046 | Human-readable resource & property guide | https://www.dmtf.org/dsp/DSP2046 |
| DSP8010 | Full schema bundle (all JSON Schema + CSDL) | https://www.dmtf.org/dsp/DSP8010 |
| DSP8011 | Standard message registries (incl. Base) | https://www.dmtf.org/dsp/DSP8011 |
| DSP2043 | Mockup bundle | https://www.dmtf.org/dsp/DSP2043 |

To mirror a DSP locally for offline work, download into this folder and add its sha256 +
retrieval date to `../REFERENCES.md`.
