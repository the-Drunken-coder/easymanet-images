# Flash Workflow

## Overview

The `easymanet flash` command writes an OpenMANET base image to an SD
card or USB drive, then places node-specific `provision.json` on the
FAT boot partition at `/easymanet/provision.json`.

## Supported Image Formats

- `.img` — raw disk image
- `.img.gz` — gzip-compressed raw disk image

## Build the Base Image

```bash
easymanet image build
```

This command uses Docker to:

1. Build a reusable Ubuntu 24.04 builder image with the OpenWrt toolchain.
2. Clone or refresh the cached OpenMANET source tree in the Docker cache.
3. Copy `images/openmanet/provisioning/openwrt-overlay/` into the firmware tree's `files/`.
4. Run `./scripts/openmanet_setup.sh -i -b ekh-bcm2711`.
5. Run `make download -jN` and `make -jN V=s`.
6. Copy the resulting `openmanet-*-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz`
   into `./dist/` by default.

By default the cache is a Docker volume. Use `--cache-dir PATH` when the host
needs to manage or persist OpenMANET cache files directly, such as in GitHub
Actions.

## macOS

### Detecting Disks

```bash
easymanet disks
```

Uses `diskutil list external` to find removable/external drives. Use
`easymanet disks --all` to include every block device.

### Flashing

```bash
easymanet flash \
  --config fleet.yml \
  --node manet02 \
  --device /dev/disk4 \
  --base-image ./openmanet-rpi4-mm6108-spi.img.gz \
  --image-sha256 <sha256> \
  --yes
```

Steps:
1. Validate config.
2. Render `provision.json` for the selected node.
3. Enforce disk safety checks (or require `--force`).
4. Unmount all partitions of the target device.
5. Stream (decompress if `.gz`) the image to the raw device via `dd`.
6. Wipe stale overlay data on partition 2 (when layout is detected; see
   [Security](#security) and [Stale overlay wipe](#stale-overlay-wipe)).
7. Mount the FAT boot partition.
8. Write `/easymanet/provision.json`.
9. Unmount and eject.

### Image verification

Downloaded images must use HTTPS and must have a SHA-256 checksum. When
configuring a download URL, provide the expected digest:

```bash
easymanet image --set-url https://example.com/openmanet.img.gz \
  --set-sha256 <sha256>
```

One-off downloaded images use the same checksum requirement:

```bash
easymanet flash --config fleet.yml --node manet02 --device /dev/disk4 \
  --image-url https://example.com/openmanet.img.gz \
  --image-sha256 <sha256> --download --yes
```

Local `--base-image` files are allowed without a checksum, but EasyMANET
prints a warning. Pass `--image-sha256 <sha256>` to verify a local image
before flashing.

### SSH at flash time

SSH (dropbear) is chosen when you flash, not in `fleet.yml`. With no SSH
flags, `management.ssh_enabled` is omitted from `provision.json` and first
boot applies the role default (gate on, point off). With
`--enable-ssh` or `--disable-ssh`, the flash command writes an explicit
`management.ssh_enabled` value for first boot.

| Flags | Result |
|-------|--------|
| (none) | Omitted from JSON; gate on, point off at first boot. |
| `--enable-ssh` | SSH on for any role. |
| `--disable-ssh` | SSH off for any role (including gates). |

`--enable-ssh` and `--disable-ssh` cannot be used together.

Example — point node with SSH:

```bash
easymanet flash --config fleet.yml --node manet02 --device /dev/disk4 \
  --base-image ./openmanet.img.gz --enable-ssh --yes
```

### Safety

- Mac internal drives (containing `/` or `/System/Volumes/Data`) are
  blocking unless `--force` is used.
- Pass the whole disk path such as `/dev/disk4`; partition paths such as
  `/dev/disk4s1` are rejected before flashing.
- `--yes` is required. Use `--dry-run` to preview.
- `--force` overrides all blocking disk warnings (system disk, large
  fixed disk, device not in the default list).

### Post-flash

After successful flash and boot-payload staging, the drive is ejected. Remove it
and insert into the Raspberry Pi.

## Linux

### Detecting Disks

```bash
easymanet disks
```

Uses `lsblk` and lists removable disks plus USB and MMC/SD-like devices.
Use `easymanet disks --all` to list every block device.

### Flashing

Same command as macOS. Streams the image with `gzip | dd` or `dd`.

### Safety

- System disks are detected via `findmnt` on `/` and `/boot` (with a
  mount-point fallback), not only partition mount lists.
- Large internal fixed disks and devices not in the default list are blocking.
- Partitions are unmounted normally before writing. If Linux reports the
  drive is busy, EasyMANET stops and asks you to close the process using the
  drive before retrying.
- `--yes` is required.
- `--force` overrides all blocking warnings.

### Permissions

Flashing requires write access to the target block device. On Linux,
members of the `disk` group may flash without root if the device is
writable. Otherwise run with `sudo`.

## Dry Run

```bash
easymanet flash \
  --config fleet.yml \
  --node manet02 \
  --device /dev/disk4 \
  --base-image ./openmanet.img.gz \
  --dry-run
```

Outputs the complete flash plan without writing anything:

- Selected node and resolved config
- Target device details
- Resolved `provision.json`
- The boot-partition payload that would be written
- Disk safety warnings (same checks as a real flash)

## Stale overlay wipe

After the base image is written, EasyMANET zeros partition 2 (the
OpenWrt rootfs/overlay region) so an old `provisioned` flag and f2fs
overlay from a previous flash cannot survive. The wipe uses the partition
layout from `diskutil` or `lsblk`, seeks to partition 2, and zeros up to
the partition size (capped at 4608 MiB). See step 6 in the flash flow
above.

## Security

Secrets from `fleet.yml` (mesh password, Wi‑Fi passwords,
`root_password_hash`, SSH public keys) are rendered into
`/easymanet/provision.json` on the **unencrypted FAT boot partition**
while you flash. Anyone with physical access to the card can read that
file until successful first boot. After provisioning, a copy lives under
`/etc/easymanet/provision.json` with mode `0600`, and `provision.sh`
removes the boot-partition copy on success.

Treat flashed SD cards and USB drives as sensitive until the node has
completed first-boot provisioning. Re-flash or securely wipe media when
decommissioning nodes.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Permission denied | Run with `sudo` |
| Device not found | Use `easymanet disks` or `easymanet disks --all`; if the path is valid but hidden, use `--force` |
| Blocking disk warning | Verify the correct device; use `--force` only if sure |
| Boot payload staging failed | Re-run the full `easymanet flash` command after verifying the boot partition mounts |
| Image won't boot | Verify the base image matches your hardware (RPi4 + MM6108 SPI) by writing it directly first, without EasyMANET injection. |
| `gzip` reports `trailing garbage ignored` for an OpenWrt/OpenMANET sysupgrade image | This is expected. OpenWrt appends sysupgrade metadata after the gzip payload. EasyMANET validates the gzip payload but allows the metadata trailer. |
| EasyMANET payload is present on the boot partition but the node still launches the normal wizard | The base image does not yet include the EasyMANET first-boot hooks. Rebuild the firmware image with `easymanet image build`, or copy `images/openmanet/provisioning/openwrt-overlay/` into the OpenMANET `files/` tree before building manually. |

EasyMANET validates `.img.gz` payloads before flashing. A corrupt cached
download is skipped during automatic image resolution and deleted before
re-download when `--download` is used. OpenWrt/OpenMANET sysupgrade
metadata appended after the gzip payload is not treated as corruption.

EasyMANET no longer attempts to edit the root filesystem offline. That
approach is invalid for standard OpenWrt/OpenMANET SquashFS images.

The current `flash` command assumes the base image already includes the
EasyMANET first-boot hooks. Those files live in this repo under:

`images/openmanet/provisioning/openwrt-overlay/`
