# EasyMANET Architecture

## Overview

EasyMANET is a provisioning layer on top of OpenMANET. It replaces the
node-local web UI setup wizard with a config-file-driven workflow. The
user defines the entire mesh fleet in one YAML file, then flashes and
provisions each node from that file.

## Data Flow

```text
fleet.yml (user writes this)
    │
    ▼
easymanet validate  ──→  catch errors before flashing
    │
    ▼
easymanet render    ──→  show resolved provision.json
    │
    ▼
easymanet flash     ──→  write base image + stage boot payload
    │
    ▼
/boot/easymanet/provision.json  (on the Pi FAT boot partition)
    │
    ▼
/etc/uci-defaults/99-easymanet  (runs once on first boot)
    │
    ▼
/usr/lib/easymanet/provision.sh  (copies JSON into overlay, applies UCI)
    │
    ▼
/etc/easymanet/provisioned  (marker file, prevents re-run)
    │
    ▼
network restart + boot report ──→ node is ready
```

## Component Responsibilities

### CLI (`cli.py`)
Entry point. Dispatches commands: `disks`, `validate`, `render`, `flash`.

### Manifest (`manifest.py`)
Parses `fleet.yml` into a structured Python object. Provides accessor
methods for mesh settings, defaults, and individual nodes.

### Validation (`validate.py`)
Checks all required fields, uniqueness constraints, IP format, SSH key
format, role values, bandwidth values, and password length. Returns
errors and warnings separately.

### Render (`render.py`)
Merges mesh settings, defaults, and node-specific overrides into a
single resolved `provision.json` document for the boot-partition payload.

### Disks (`disks.py`)
Lists available external/removable disks on macOS (diskutil) and Linux
(lsblk). Detects system disks and mounted partitions.

### Image (`image.py`)
Streams `.img` or `.img.gz` to the target block device with progress
display. Handles unmounting, sync, and eject.

### Inject (`inject.py`)
Mounts only the FAT boot partition after flashing and writes the
node-specific `/easymanet/provision.json` payload there.

### First-boot scripts (`provisioning/openwrt-overlay/`)
Shipped in the OpenWrt `files/` overlay and baked into the firmware image:
- `etc/uci-defaults/99-easymanet`: UCI defaults trigger that calls `provision.sh`.
- `usr/lib/easymanet/provision.sh`: Generic shell script that finds the
  boot-partition payload, copies it into overlay storage, and applies
  UCI/OpenMANET configuration automatically.
- `usr/lib/easymanet/network.sh`, `boot-report.sh`: helpers for management
  LAN repair and post-boot diagnostics (see init.d hooks in the same overlay).

## Design Principles

1. **Config-file-driven**: All configuration comes from `fleet.yml`.
   No interactive prompts, no web UI.
2. **Generic first boot**: The shell scripts are node-agnostic. Only
   the boot-partition `provision.json` changes per node.
3. **Explicit safety**: Never auto-select a disk. Require `--yes`.
   Detect and warn about system disks.
4. **Idempotent provision**: The first-boot script checks for
   `/etc/easymanet/provisioned` and skips if already provisioned.

## File Layout on Flashed Drive

```text
/boot/
    easymanet/
        provision.json      ← generated from fleet.yml for this node

/etc/easymanet/
    provision.json          ← copied from boot partition on first boot
    provisioned             ← created by provision.sh on success

/etc/uci-defaults/
    99-easymanet            ← triggers provision.sh on first boot
                              (deleted by OpenWrt after execution)

/usr/lib/easymanet/
    provision.sh            ← generic provisioning script
```

## Firmware Build Requirement

The active flash workflow assumes the base OpenMANET image already
contains the EasyMANET first-boot hooks. Build those files into the
firmware image using the OpenWrt/OpenMANET `files/` overlay mechanism.
This repo keeps the reusable overlay under:

`provisioning/openwrt-overlay/`
