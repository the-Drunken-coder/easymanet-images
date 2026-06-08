# EasyMANET Documentation Index

This is the entry point for project documentation. Keep durable reference
material here, and use the short-lived problem inbox for branch-specific
blockers that another agent or developer may need to pick up.

## Project Docs

| Location | What it holds | Use it when... |
| --- | --- | --- |
| [`docs/architecture.md`](architecture.md) | End-to-end provisioning flow and component responsibilities. | "How is EasyMANET wired together?" |
| [`docs/monorepo.md`](monorepo.md) | Private monorepo layout, product surfaces, and public export behavior. | "Where does each product surface live?" |
| [`docs/release.md`](release.md) | Version policy, release checklist, and artifact commands. | "How do we cut the first release?" |
| [`docs/sample-fleet.md`](sample-fleet.md) | Starter fleet YAML and workspace copy command. | "What should my first fleet file look like?" |
| [`docs/manifest.md`](manifest.md) | Reference for every field in `fleet.yml`. | "What config shape does this project accept?" |
| [`docs/flashing.md`](flashing.md) | Host-side flashing and provisioning guide. | "How do I safely flash a node?" |
| [`docs/openmanet-config-investigation.md`](openmanet-config-investigation.md) | Notes on OpenMANET wizard behavior and config files. | "What does OpenMANET expect on the device?" |
| [`docs/lessons-learned.md`](lessons-learned.md) | Recurring hardware, build, and debugging gotchas from real node work. | "What should a future session know before touching this?" |
| [`docs/design-decisions/`](design-decisions/) | Durable architectural and implementation choices. | "What did we decide, and why?" |
| [`docs/problems/`](problems/) | Short-lived agent-to-agent notes on active blockers. | "What is broken right now on this branch?" |
| [`future concepts and plans/`](../future%20concepts%20and%20plans/) | Exploratory ideas and future product direction. | "What might we build later?" |

Start templates:
[`design-decisions/_EXAMPLE_DESIGN_DECISION_.md`](design-decisions/_EXAMPLE_DESIGN_DECISION_.md),
[`problems/_EXAMPLE_PROBLEM_.md`](problems/_EXAMPLE_PROBLEM_.md).

## Developer Workflow

- Log branch-specific blockers in `docs/problems/` when the next session needs
  concrete context to continue.
- Promote policy, architecture, or hardware-behavior choices to
  `docs/design-decisions/` once the problem turns into a durable decision.
- Move recurring operational gotchas to `docs/lessons-learned.md` after the
  same mistake or surprise appears more than once.

## Root Files

- **`README.md`** - project overview, quick start, and command map.
- **`pyproject.toml`** - package metadata, entry points, source roots, and test dependencies.
