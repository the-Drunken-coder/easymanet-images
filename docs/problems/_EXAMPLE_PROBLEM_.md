# Problem Template

Each entry under `docs/problems/` is a short-lived note for agent-to-agent
reference — most are resolved in minutes; none should live longer than a day or
two. Use this template to keep the format consistent:

1. **Time & Date:** [UTC timestamp or local time zone timestamp]
2. **Name:** [One-line summary identifier]
3. **Issue:** [Short description of the observable problem]
4. **Severity:** [S1–S5 label from **Severity Levels** below]
5. **Location:** [Component and specific file/folder path associated with the issue]
6. **Expected:** [What should happen]
7. **Actual:** [What happens instead]
8. **Reproduction:** [Numbered steps, or "single command / test name" when that's enough]
9. **Notes:** [Optional — investigation hints, error snippets, links; skip if empty]

## What belongs here

- Problems hit while building, testing, flashing, or debugging — logged so the next agent session can pick up context quickly.
- Resolved or abandoned problems can stay in place as reference; no status tracking needed.

### What does not belong here

- **Recurring agent confusion** → `docs/lessons-learned.md` (after you've seen the same gotcha more than once).
- **Architectural decisions and durable design** → `docs/design-decisions/`.
- **How the system is supposed to work** → the docs listed in [README.md](../../README.md#docs).

### Severity Levels

- **S1 (Blocker):** Wrong data, security issue, or completely blocks the current task (dev, CI, flash workflow, or node won't provision).
- **S2 (Major):** Core path broken with no reasonable workaround (e.g. `validate` / `flash` / first-boot provisioning).
- **S3 (Moderate):** Broken edge case or painful workaround exists.
- **S4 (Minor):** Annoyance, docs drift, flaky test — task can continue.
- **S5 (Note):** Worth recording for the next agent; no real impact on the current work.

### Example

1. **Time & Date:** 2026-05-30T18:00:00Z
2. **Name:** `easymanet render` omits gateway block for gate role
3. **Issue:** Rendered `provision.json` missing `gateway` when node role is `gate` and `gateway.enabled` is true in fleet YAML
4. **Severity:** S2 (Major)
5. **Location:** `easymanet/render.py`, `tests/test_render.py`
6. **Expected:** `easymanet render fleet.yml` includes gateway settings for gate nodes matching `easymanet validate` output
7. **Actual:** Gateway section absent from rendered JSON; first boot leaves node without uplink
8. **Reproduction:**
   1. Use `examples/three-node-field-mesh.yml` (or a minimal fleet with one `gate` node and `gateway.enabled: true`)
   2. Run `easymanet render fleet.yml`
   3. Compare output to node `provision.json` schema in `docs/manifest.md`
9. **Notes:** Check render path vs inject/overlay expectations in `provisioning/openwrt-overlay/usr/lib/easymanet/provision.sh`.

### File naming

- Keep `_EXAMPLE_PROBLEM_.md` as the style guide; do not edit it for real incidents.
- Add one markdown file per issue, named `YYYY-MM-DD-short-slug.md` (e.g., `2026-05-30-render-gateway-omission.md`).

### Typical locations (EasyMANET)

| Area | Paths |
| --- | --- |
| CLI / Python | `easymanet/` (`cli.py`, `validate.py`, `render.py`, `build.py`, `image.py`, `inject.py`, …) |
| First-boot (host staging) | `firstboot/` |
| OpenWrt overlay | `provisioning/openwrt-overlay/` (`etc/uci-defaults/`, `usr/lib/easymanet/`) |
| Tests | `tests/` |
| CI | `.github/workflows/` |
| Fleet examples | `examples/*.yml` |
