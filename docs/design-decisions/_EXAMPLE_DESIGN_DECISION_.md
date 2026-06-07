# Design Decision Template

Each entry under `docs/design-decisions/` records a durable architectural or
implementation choice for EasyMANET as a whole: Python CLI, OpenWrt overlay,
first-boot behavior, flashing safety, tests, docs, or hardware assumptions. Use
this template to keep the format consistent.

1. **Time & Date:** [UTC timestamp or local time zone timestamp]
2. **Name:** [One-line summary identifier]
3. **Context:** [What problem or constraint prompted the decision]
4. **Decision:** [What was chosen]
5. **Alternatives considered:** [Other options and why they were rejected]
6. **Consequences:** [Trade-offs, follow-on work, or constraints introduced]
7. **Location:** [Relevant files, scripts, docs, or hardware affected]
8. **Notes:** [Optional - links, related decisions, or open questions; skip if empty]

## What belongs here

- Durable architecture and implementation choices for the project.
- Hardware, networking, or flashing assumptions that future contributors need to
  understand before changing related code.
- Decisions promoted from `docs/problems/` once the issue is understood and the
  remaining work is a policy or architecture choice.

### What does not belong here

- **Transient bugs or blockers** -> `docs/problems/`.
- **Recurring agent confusion or operational gotchas** -> `docs/lessons-learned.md`.
- **Config reference** -> `docs/manifest.md`.
- **Operational how-to** -> `docs/flashing.md` or the relevant README.

### File naming

- Keep `_EXAMPLE_DESIGN_DECISION_.md` as the style guide; do not edit it for
  real decisions.
- Add one markdown file per decision, named `YYYY-MM-DD-short-slug.md` (e.g.,
  `2026-06-06-ethernet-management-and-wan-share-eth0.md`).
