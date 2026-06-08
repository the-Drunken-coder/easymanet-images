# EasyMANET Lessons Learned

This file captures practical lessons from getting an EasyMANET-flavored OpenMANET
image built, flashed, booted, and reachable on a Raspberry Pi 4 with a Seeed /
Morse MM6108 SPI HaLow HAT. Future agent sessions should read this before
changing the firmware build, first-boot provisioning, flashing flow, or network
debugging path.

## Build Workflow

- Full OpenMANET image builds are slow on GitHub-hosted runners. A successful
  full overlay build can take around 2-5 hours depending on cache state.
- The slow part is the OpenWrt/OpenMANET firmware build, not the EasyMANET
  Python code or overlay scripts.
- Historical bisect builds (stock OpenMANET, empty overlay, full overlay) were
  all in the same broad runtime range. The overlay itself was not the main
  source of build time.
- Normal development is covered by the `CI` workflow on push and pull request
  (`pytest` plus overlay shell syntax checks). Run the same checks locally with:

  ```sh
  pytest -q
  sh -n images/openmanet/provisioning/openwrt-overlay/usr/lib/easymanet/provision.sh
  sh -n images/openmanet/provisioning/openwrt-overlay/usr/lib/easymanet/network.sh
  sh -n images/openmanet/provisioning/openwrt-overlay/usr/lib/easymanet/boot-report.sh
  ```

- Only run the full firmware workflow when a flashable image is needed:

  ```sh
  gh workflow run build-openmanet-image.yml \
    -f openmanet_version=1.6.5 \
    -f board=ekh-bcm2711 \
    -f target=rpi4-mm6108-spi \
    -f openwrt_target=bcm27xx \
    -f subtarget=bcm2711 \
    -f jobs=2
  ```

- The `jobs` input caps parallel `make` jobs (default `2` on hosted runners).

## Artifact Selection

- The `ekh-bcm2711` build produces multiple Raspberry Pi 4 images. For the
  Seeed/Morse SPI HAT tested here, use:

  ```text
  openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz
  ```

- Do not accidentally flash the SDIO or USB variant unless the hardware really
  matches it.
- Artifacts are downloaded from GitHub Actions with:

  ```sh
  gh run download <run-id> --dir /tmp/easymanet-artifact-<run-id>
  ```

## Flashing

- The current flashing command shape is:

  ```sh
  sudo easymanet flash \
    --config examples/fleet.yml \
    --node manet01 \
    --device /dev/disk4 \
    --base-image /tmp/easymanet-artifact-<run-id>/openmanet-1.6.5-rpi4-mm6108-spi/openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz \
    --yes
  ```

- On macOS, verify the target USB drive before flashing:

  ```sh
  diskutil list external physical
  ```

- The USB stick used during testing appeared as:

  ```text
  /dev/disk4
  Model: USB DISK 3.0
  Removable: yes
  ```

- If macOS shows the USB device in `system_profiler SPUSBDataType` but not in
  `diskutil list`, unplug and replug it. Do not flash until a real
  `/dev/diskN` appears.
- The flash tool patches `/boot/cmdline.txt` to use `root=PARTUUID=...-02`.
  This mattered for USB boot on the Pi.
- `gzip: ... trailing garbage ignored` appeared during successful flashes. It
  was noisy but not fatal in the tested path.
- Avoid shell wrapping mistakes:
  - There must be a space between `flash` and `--config`.
  - Do not split the `--base-image` path across lines unless the shell line
    continuation is exact.
  - Do not paste conversational words like `lets` into the command.

## First Boot Diagnostics

- Offline boot reports are essential when SSH is not available.
- The EasyMANET image writes reports to the FAT boot partition under:

  ```text
  /easymanet/boot-report-latest/
  /easymanet/boot-report-<timestamp>/
  ```

- Useful report files:
  - `summary.txt`
  - `brctl-show.txt`
  - `ip-addr.txt`
  - `ip-link.txt`
  - `ip-route.txt`
  - `uci-network.txt`
  - `uci-wireless.txt`
  - `config-network`
  - `config-wireless`
  - `logread.txt`
  - `easymanet-network.log`

- If a report is captured too early, it may show pre-repair network state. The
  management LAN repair waits before running; wait at least 90-120 seconds
  before pulling the drive for diagnostics.

## RPi4 Ethernet Management

- The initial failure mode was:
  - SSH to `10.41.254.1` timed out.
  - `br-lan` had `10.41.254.1/16`.
  - `br-lan` had no interfaces attached.
  - `eth0` was configured as `wan`.

- The working state is:

  ```text
  network.@device[0].name='br-lan'
  network.@device[0].type='bridge'
  network.@device[0].ports='eth0'
  network.lan.device='br-lan'
  network.lan.ipaddr='10.41.254.1'
  ```

  and:

  ```text
  br-lan ... interfaces: eth0
  eth0 ... master br-lan
  br-lan inet 10.41.254.1/16
  ```

- The current fix is intentionally defensive:
  - first-boot provisioning tries to keep `eth0` on `br-lan`
  - a late boot repair service runs after startup and enforces the same state
  - the repair removes stale direct-`eth0` `wan` / `wan6`, commits network
    config, brings `lan` up, and calls `brctl addif br-lan eth0`
  - gate nodes with `gateway.uplink_interface: eth0` run WAN DHCP on `br-lan`,
    so wired management and Ethernet upstream share one L2 segment

- This was necessary because OpenMANET startup can leave `eth0` as `wan`, which
  makes direct Ethernet management unreachable even though Dropbear is running.

## SSH and Login

- Use the `root` user:

  ```sh
  ssh root@10.41.254.1
  ```

- A previous failed login was caused by trying to SSH as the local macOS user
  instead of `root`.
- The test image currently has no root password set, so OpenMANET prints a
  warning. This is acceptable for bring-up, but should be fixed before any
  real deployment.

## Radio Detection and Wireless

- Do not assume the HaLow radio is always `radio0`, `radio1`, or `radio2`.
- The provisioner should detect the Morse/802.11ah radio dynamically:
  - prefer `wireless.<radio>.type='morse'`
  - fall back to `wireless.<radio>.hwmode='11ah'`

- The local AP should not be put on the Morse radio unless that is intentional.
  The provisioner should choose a `mac80211` radio for local AP when available.
- On the tested Pi/HAT, reports showed the mesh interface as:

  ```text
  wlan0 inet 10.41.1.1/24
  mesh_id easymanet-field
  channel 42
  bandwidth 2
  country US
  ```

- The local AP path may be unreliable on this Pi/HAT combo; do not use AP
  visibility as the only boot success signal.

## Two-Node Mesh Test

- `examples/fleet.yml` already defines multiple nodes on the same mesh:
  - `manet01`: `10.41.1.1`, role `gate`
  - `manet02`: `10.41.2.1`, role `point`

- To test two radios:
  1. Flash one USB stick with `--node manet01`.
  2. Flash another USB stick with `--node manet02`.
  3. Boot both devices.
  4. SSH into `manet01` over Ethernet:

     ```sh
     ssh root@10.41.254.1
     ```

  5. From `manet01`, test mesh reachability:

     ```sh
     ping 10.41.2.1
     ssh root@10.41.2.1
     ```

- `manet02` is a point node and may not expose the same Ethernet management
  path unless the manifest explicitly configures it that way.

## Troubleshooting Order

When a flashed device does not respond:

1. Confirm the correct image target was flashed (`rpi4-mm6108-spi` for this
   hardware).
2. Confirm the drive was flashed with the intended node name.
3. Wait at least 90-120 seconds after boot.
4. Try `ssh root@10.41.254.1` for `manet01`.
5. If SSH fails, pull the drive and inspect:

   ```sh
   ls -R /Volumes/boot/easymanet
   cat /Volumes/boot/easymanet/boot-report-latest/summary.txt
   cat /Volumes/boot/easymanet/boot-report-latest/brctl-show.txt
   cat /Volumes/boot/easymanet/boot-report-latest/ip-addr.txt
   cat /Volumes/boot/easymanet/boot-report-latest/uci-network.txt
   cat /Volumes/boot/easymanet/boot-report-latest/easymanet-network.log
   ```

6. If `br-lan` has no `eth0`, the management LAN repair did not run or ran too
   late for the report. Check `ps.txt`, `logread.txt`, and
   `easymanet-network.log`.

## Stock OpenMANET Setup (Without EasyMANET)

Lessons from flashing and booting a stock OpenMANET image on a Raspberry Pi 4
with a Seeed Studio MM6108 SPI HAT, booting from USB.

### Image Download

- Download stock images from [OpenMANET/firmware releases](https://github.com/OpenMANET/firmware/releases).
- For RPi4 + Seeed Studio MM6108 SPI, use:
  ```text
  openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz
  ```
- Always verify the SHA256 checksum against the release page. The download
  can appear complete but be corrupt. Use `shasum -a 256 <file>`.

### USB Boot: Critical cmdline.txt Fix

- Stock OpenMANET images hardcode `root=/dev/mmcblk0p2` in `cmdline.txt`,
  which only works for SD card boot. The kernel will fail to find the root
  filesystem on USB.
- **Fix**: Change `root=/dev/mmcblk0p2` to `root=/dev/sda2` on the boot
  partition after flashing.
- Without this fix, the Pi appears to power on but never becomes reachable.
  ARP shows `(incomplete)` for the expected IP.

### macOS Flashing

- Always unmount partitions before `dd`: `diskutil unmountDisk /dev/disk4`
- macOS `dd` error `Resource busy` means partitions are still mounted.
- After `dd`, macOS may lose track of the disk. Unplug and replug for the
  OS to re-read the partition table.
- The "trailing garbage ignored" warning from `gunzip` is normal — OpenWrt
  appends sysupgrade metadata after the gzip payload. The image is not
  corrupt.
- The `.img.gz` is a sparse image: ~119MB of compressed data, but the
  partition table declares a 4.3GB root partition. Only the actual data
  (~119MB) is written by `dd`.

### First Boot Timing

- Fresh flash first boot: wait at least 2 minutes. The node expands
  filesystems and runs initial setup.
- The node gives out DHCP leases immediately after the kernel boots, but
  SSH/web UI may not be available until the boot scripts finish.
- Default IP: `10.41.254.1`. Default hostname: `manet02`.
- SSH: `ssh root@10.41.254.1` (no root password by default).

### After Setup Wizard: IP Changes

- Running the OpenMANET setup wizard triggers auto-addressing. The node
  **changes its IP** and may reboot.
- After the wizard, you **must renew your DHCP lease** on the host machine
  to get a new IP in the node's range:
  ```bash
  networksetup -setdhcp "<interface-name>"
  ```
- Use `networksetup -listallnetworkservices` to find your Ethernet
  adapter name (e.g., `AX88179B` for a USB-C Ethernet dongle).
- The auto-assigned IP can be unexpected (e.g., `10.41.254.0/16`), but
  it's valid as a host address because of the wide `/16` netmask.

### Finding the Node After IP Changes

- mDNS is the most reliable way to find the node: `ssh root@manet02.local`
- The web UI is also available at `http://manet02.local`
- `arp -a` can show stale ARP entries. Combine with `ping` scans if mDNS
  is unavailable.
- If the host has a stale SSH host key from a previous flash, clear it:
  ```bash
  ssh-keygen -R 10.41.254.1
  ssh-keygen -R manet02.local
  ```

### DHCP Lease Renewal Without Sudo (macOS)

- `sudo ipconfig set en7 DHCP` requires `sudo`.
- Use `networksetup -setdhcp "<service-name>"` instead — works without
  `sudo` for the current user.
- Find the service name first: `networksetup -listallnetworkservices`.

### Network Interfaces on Stock OpenMANET

- The main bridge is `br-ahwlan`, not `br-lan`.
- `eth0` is bridged into `br-ahwlan` for Ethernet management.
- The HaLow radio appears as `morse0` (type: Morse MM6108) — it may show
  as DOWN before mesh configuration.
- Onboard Wi-Fi appears as `phy1-ap0` and can serve as a local AP.

### Flashing Second/Additional Nodes

- Flash the same image to a second USB drive, apply the same `cmdline.txt`
  fix, and boot.
- Each node auto-assigns its own unique IP on the mesh. No hostname
  conflicts — the hostname is baked into the image and shared.
- Run the setup wizard on each node to configure mesh parameters
  (channel, password, etc.). Nodes on the same mesh will automatically
  peer and form the mesh after config.

## Mesh Troubleshooting

These lessons came from getting two RPi4 + Seeed MM6108 SPI nodes to form a
mesh for the first time.

### Critical: BCF Board Config File

- **Without `bcf='bcf_fgh100mhaamd.bin'` in `wireless.radio2` UCI config,
  the Morse MM6108 radio reports `txpower 0.00 dBm` and cannot transmit.**
  Beacons and data frames are not sent, making the node invisible to peers.
- The first node had this option set automatically by the setup wizard. The
  second node did not — possibly due to a wizard timing issue or different
  code path during setup.
- **Fix**: Add the BCF option and restart wireless:
  ```bash
  uci set wireless.radio2.bcf='bcf_fgh100mhaamd.bin'
  uci commit wireless
  wifi
  ```
- After adding BCF, txpower jumped from `0.00 dBm` to `27.00 dBm` and the
  peer was immediately visible.
- **Always verify txpower after setup**: `iw dev wlan0 info | grep txpower`

### SAE Authentication Failures

- `MESH-SAE-AUTH-FAILURE addr=<mac>` in wpa_supplicant_s1g logs means the
  SAE password exchange failed. Causes in order of likelihood:
  1. **Radio txpower is 0.00 dBm** (missing BCF) — one node can send SAE
     commit frames but the other cannot respond.
  2. **Mesh ID mismatch** — `MESH ID` in scan output must match `mesh_id`
     in UCI config on both nodes.
  3. **SAE password mismatch** — `wireless.default_radio2.key` must be
     identical on both nodes.

### Diagnosing Mesh Peer Issues

- Check if radios see each other at all:
  ```bash
  iw dev wlan0 scan -u | grep -E 'BSS|signal|MESH ID'
  ```
  On Morse S1G radios, scans may return empty in mesh mode even when the
  peer is visible — the driver handles discovery internally and reports
  peers via wpa_supplicant events.

- Check peer status:
  ```bash
  iw dev wlan0 station dump  # Look for mesh plink: ESTAB vs LISTEN
  batctl n                    # batman-adv neighbor table
  batctl o                    # batman-adv originator table
  ```

- Check wpa_supplicant_s1g logs for peer events:
  ```bash
  logread | grep -i 'wpa_supplicant_s1g\|MESH-SAE\|new peer\|plink'
  ```

- Useful peer states to recognize:
  - `mesh plink: LISTEN` — waiting for peer OPEN, SAE not started
  - `mesh plink: ESTAB` — peer link established
  - `authenticated: no` — SAE handshake incomplete
  - `tx retries` / `tx failed` — high counts mean radio transmission issues

### Mesh ID Diagnostic

- The mesh ID is distinct from the SSID. In UCI:
  ```bash
  uci get wireless.default_radio2.ssid     # e.g. "manet02"
  uci get wireless.default_radio2.mesh_id  # e.g. "manet02"
  ```
- The setup wizard defaults the mesh ID to the hostname (`manet02`).
- The scan reveals the peer's mesh ID: `iw dev wlan0 scan | grep "MESH ID"`
- **Both mesh ID and SSID must match across nodes for the mesh to form.**

### S1G Channel Mapping

- The Morse MM6108 operates in the sub-1GHz (802.11ah/s1g) band but the
  driver reports a mapped 5GHz channel to the kernel.
- S1G channel 42 maps to HT channel 159 (~5785 MHz) internally:
  ```text
  wpa_supplicant_s1g: S1G mapped HT channel 159
  ```
- This is normal — the kernel and iw see a 5GHz channel, but the radio
  transmits in the actual 900 MHz s1g band.

### wpa_supplicant_s1g

- OpenMANET uses a custom `wpa_supplicant_s1g` binary (not standard
  `wpa_supplicant`) for the Morse s1g radio.
- Config file is at `/var/run/wpa_supplicant-wlan0.conf` (note: not
  `wpa_supplicant_s1g-wlan0.conf` despite the binary name).
- The CLI tool is `wpa_cli_s1g -i wlan0` for checking mesh status.
- SAE config: `sae_pwe=1` (hash-to-element) and `ieee80211w=2` (PMF
  required) are standard for OpenMANET mesh.

### IPv6 Link-Local as a Mesh Health Check

- When IPv4 pings fail but batman-adv shows neighbors, test with IPv6
  link-local — it bypasses IP addressing issues:
  ```bash
  ping6 fe80::<peer-lladdr>%br-ahwlan
  ```
- A successful IPv6 ping over the mesh confirms Layer 2 forwarding works
  and isolates the problem to IPv4 config.
- The peer's link-local address is in `ip neigh` output.

### Known Multi-Node Issues

- Both nodes share the same hostname (`manet02`) by default. This causes
  mDNS conflicts (`manet02.local` resolves inconsistently) and ARP table
  warnings (`name already known`).
- After the setup wizard, the auto-assigned IP may be `10.41.254.0` — this
  is a valid host address with `/16` netmask but may confuse networking
  tools.
- The `batctl n` MAC change warnings (`changing mac from X to Y`) are
  cosmetic — batman-adv tracks interfaces by name; these appear when
  flashing the same image to a second device with the same interface names.

## Open Mesh (No SAE) Peer Discovery Debugging

A parallel troubleshooting path was attempted where both nodes were configured
for open mesh (no SAE encryption) with manually applied settings, bypassing
the setup wizard. This hit the same peer discovery barrier despite correct
settings on the surface.

### Settings Applied

The following known-good settings were manually configured on both nodes:

- `mesh_fwding=0` in the generated wpa_supplicant config.
- `wireless.mesh0.ifname='wlan0'` so mesh11sd recognizes the interface.
- Both nodes joined `bat0` and appeared in the BATMAN topology.
- Both nodes configured as open mesh (no SAE/encryption).

### Blocking State

Despite the above being correct:

- `mesh11sd status` on the first node showed `active_peers=0`,
  `active_stations=0`.
- `batctl n` showed no neighbors.
- `iw dev wlan0 station dump` was empty on the connected node.
- Logs confirmed each node started its mesh group (`MESH-GROUP-STARTED`),
  but the 802.11s peer link never formed.

### Root Cause Assessment

The problem was **not** IP routing, BATMAN attachment, or mesh11sd
recognizing the interface. It was the underlying **Morse 802.11s peer
discovery/peering** layer. The two nodes were each forming their own
isolated mesh group despite having the same mesh ID and channel.

The most likely remaining difference was a Morse/OpenMANET wizard-generated
setting that had not been replicated manually, or a driver-level
configuration parameter required by the Morse MM6108 for 802.11s peering
to function.

### Recommended Diagnostic Approach

To identify the missing setting:

1. Capture the full configuration from a **wizard-configured node** (known
   to work):
   ```bash
   cat /etc/config/wireless
   cat /etc/config/network
   cat /etc/config/mesh11sd
   cat /var/run/wpa_supplicant-wlan0.conf
   ```

2. Diff against the manually configured node. The exact difference should
   be small — likely a single Morse-specific UCI option or wpa_supplicant
   parameter.

3. Alternatively, ask OpenMANET or Morse Micro maintainers for the
   **known-good rpi4-mm6108-spi mesh config** — they know which
   driver-specific parameters are required for 802.11s peer discovery
   on MM6108 hardware.

### Known Missing Options That Can Cause This

Based on the successful SAE mesh (previous section), these options were
critical for peer discovery:

| Setting | Effect When Missing |
|---------|-------------------|
| `wireless.radio2.bcf='bcf_fgh100mhaamd.bin'` | Radio txpower 0.00 dBm, cannot transmit |
| `wireless.default_radio2.mesh_id` mismatch | Nodes form separate mesh groups |
| `wireless.default_radio2.key` mismatch (SAE) | SAE authentication fails repeatedly |
| Mismatched `channel` or `band` | Radios on different frequencies |

The BCF file setting is the most overlooked — it's a board-specific
firmware parameter that the Morse driver requires to initialize the
radio hardware correctly. Without it, the radio loads but does not
function at the PHY level, which matches the symptoms seen here.

## Known Follow-Ups

- Set a real root password or valid authorized keys before non-lab use.
- Consider making Ethernet management available on point nodes during bring-up.
- Extend CI beyond unit tests if overlay shell checks need more coverage.
- Consider a self-hosted runner for full OpenMANET image builds.
- Stock OpenMANET images should support USB boot out of the box — consider
  reporting the `cmdline.txt` SD card hardcode as an issue upstream.
- After setup wizard, OpenMANET could display the new IP before rebooting
  to reduce confusion.

## 2026-05-11 Handoff: <wifi-ssid> Wi-Fi Uplink Bring-Up

This section captures the latest state before handing work to a new agent.

Date captured: 2026-05-11.

### Current Build/Repo State

- Branch: `add-project-files`
- Latest pushed commit:
  ```text
  16ea990 Fix Wi-Fi uplink radio and stale overlay flashing
  ```
- New GitHub Actions image build:
  ```text
  https://github.com/the-Drunken-coder/easymanet/actions/runs/25678707977
  ```
- That build was `queued` when last checked.
- Local uncommitted file:
  ```text
  docs/lessons-learned.md
  ```
- Local ignored file with private Wi-Fi credentials:
  ```text
  examples/fleet.yml
  ```

### Wi-Fi Credentials Used Locally

The local ignored `examples/fleet.yml` contains:

```yaml
gateway:
  wifi:
    enabled: true
    ssid: <wifi-ssid>
    password: "<wifi-password>"
    encryption: psk2
```

The gateway node uses:

```yaml
gateway:
  enabled: true
  uplink_interface: wifi
```

Do not commit the local `examples/fleet.yml`; it is intentionally ignored.

### Gateway State

The gateway drive was flashed as `manet01` from build artifact
`25635557305`, then manually repaired over SSH because stale overlay data
survived the flash.

Confirmed working gateway state after manual repair:

- Hostname: `manet01`
- <wifi-ssid> Wi-Fi IP:
  ```text
  192.168.1.55
  ```
- SSH command:
  ```bash
  ssh root@192.168.1.55
  ```
- `ifstatus wan` showed DHCP up on `phy1-sta0`.
- `phy1-sta0` had:
  ```text
  192.168.1.55/24
  ```
- `wpa_supplicant` logs showed successful association and WPA key
  negotiation with SSID `<wifi-ssid>`.
- Mesh interface was up as `easymanet-field`.
- `batctl n` showed no neighbors yet, so `manet02` was not joined/reachable
  at that time.

### Root Causes Found

#### 1. Stale OpenWrt overlay survived re-flash

The OpenMANET `.img.gz` payload writes only the compressed image contents.
It does **not** necessarily overwrite the whole USB drive. A prior writable
OpenWrt overlay survived across flashes and caused old files to persist:

- `/etc/easymanet/provisioned`
- `/etc/easymanet/provision.json`
- `/usr/lib/easymanet/provision.sh`

Observed symptom:

- `/boot/easymanet/provision.json` had the new <wifi-ssid> payload.
- `/etc/easymanet/provision.json` still had the old no-Wi-Fi payload.
- `/etc/easymanet/provisioned` caused the installed first-boot script to
  skip the new boot payload.

Fix committed in `16ea990`:

- `packages/core/src/easymanet/image.py` calls `_clear_stale_overlay(device)` **after**
  writing the image (so the new partition table is in place).
- `_clear_stale_overlay` uses `get_partition2_wipe_range()` to seek to
  partition 2 and zero that region (up to the partition size, capped at
  4608 MiB), instead of only the first 512 MiB of the whole disk.

See [flashing.md](flashing.md) for the current flash flow and stale
overlay wipe behavior.

This should force clean first-boot behavior on future flashes.

#### 2. Wi-Fi uplink selected the wrong radio

EasyMANET originally selected the first `mac80211` device for local AP/Wi-Fi
uplink. On this Pi image, `radio0` exists in UCI but has no usable PHY:

```text
netifd: radio0: Could not find PHY for device 'radio0'
netifd: radio0: Bug: PHY is undefined for device 'radio0'
```

The real onboard Wi-Fi was `radio3`, with path containing:

```text
platform/soc/fe300000.mmcnr/mmc_host/mmc1/mmc1:0001/mmc1:0001:1
```

Manual repair that made <wifi-ssid> work:

```sh
uci set wireless.radio3.disabled=0
uci set wireless.wan0.device=radio3
uci -q set wireless.ap0.disabled=1
uci commit wireless
wifi reload
```

Fix committed in `16ea990`:

- `find_local_ap_radio` now prefers `mac80211` radios whose path contains
  `mmc_host` or `mmc1`.
- When `gateway.wifi.enabled` is true, EasyMANET does not create the local
  AP on the same client radio.
- It deletes `wireless.ap0` before creating the Wi-Fi STA uplink.

### Important Network Detail

When the Mac was Ethernet-connected to the gateway, the gateway's DHCP could
install a default route via the Pi:

```text
default 10.41.0.1 en7
```

That broke internet/GitHub access from the Mac. Fix on macOS:

```bash
sudo route -n delete default 10.41.0.1
sudo route -n add default 192.168.1.1
```

This is expected with the shared-`br-lan` `eth0` gateway mode: the Mac sees the
EasyMANET management DHCP service and the upstream Ethernet network on the same
wire.

### What To Do Next

When build `25678707977` finishes:

1. Download the new artifact and copy the SPI image into `dist/`.
2. Reflash both USB drives with the new image. This time the flash command
   should zero stale overlay first.
3. Flash `manet01` gateway:
   ```bash
   sudo easymanet flash \
     --config examples/fleet.yml \
     --node manet01 \
     --device /dev/diskX \
     --base-image dist/openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz \
     --yes
   ```
4. Flash `manet02` point:
   ```bash
   sudo easymanet flash \
     --config examples/fleet.yml \
     --node manet02 \
     --device /dev/diskX \
     --base-image dist/openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz \
     --yes
   ```
5. Boot both radios.
6. Check <wifi-ssid> LAN for SSH:
   ```bash
   ping -c 3 manet01.local
   ping -c 3 manet02.local
   for i in $(seq 1 254); do
     (nc -G 1 -z 192.168.1.$i 22 >/dev/null 2>&1 && echo 192.168.1.$i) &
   done
   wait
   ```
7. SSH to gateway and verify:
   ```sh
   cat /proc/sys/kernel/hostname
   ifstatus wan
   uci show wireless.wan0
   batctl n
   batctl o
   iw dev wlan0 station dump
   ```

Expected success criteria:

- `manet01` reachable over <wifi-ssid> Wi-Fi.
- `manet02` also reachable over <wifi-ssid> Wi-Fi if the STA uplink is
  enabled for point nodes via defaults.
- `batctl n` on `manet01` shows the other node.
- `iw dev wlan0 station dump` shows the peer and ideally `mesh plink: ESTAB`.

## 2026-05-12: Do Not Auto-Reboot From First-Boot Provisioning

`provision.sh` previously ended with `( sleep 5; reboot ) &`. On the
`rpi4-mm6108-spi` build that triggered a boot loop:

- `S10boot` (which runs the uci-defaults `99-easymanet` hook) takes
  ~18-20 seconds. The 5-second timer expires before the rest of the OpenWrt
  init sequence reaches `S50dropbear`, the late management-LAN repair, or the
  `easymanet-boot-report` `sleep 20` service. `reboot` then kills them.
- The reboot also pre-empts the f2fs checkpoint of the overlay, so
  `/etc/easymanet/provisioned` and the deletion of `/etc/uci-defaults/99-easymanet`
  are lost. The next boot re-runs provisioning, then reboots again. en7 link
  flap, port 22 connection-refused, port 53 transiently open.

Diagnostic fingerprint in `/Volumes/boot/easymanet/`:

- Multiple `boot-report-*` directories all with `reason=provisioned` and uptimes
  under ~25s; no reports with `reason=init` or `reason=post-management-lan`.
- Each report's `easymanet.log` starts fresh with `provisioning started`
  rather than `Already provisioned, skipping`.
- `ps.txt` shows `S10boot` and `provision.sh` still running, no `dropbear`,
  no syslogd (`logread.txt` is just "Failed to find log object").

Fix: removed the auto-reboot in commit `576b888`. Provisioning relies on
`/etc/init.d/network restart` (already in `provision.sh`) plus the normal
power-cycle by the operator. Test `test_firstboot_does_not_auto_reboot_after_provisioning`
locks this in.

Verified post-fix on `manet01` (uptime ~110s after first boot):

- `ssh root@10.41.254.1` worked on the first try; `pgrep -a dropbear` showed
  the listener.
- `/boot/easymanet/` had a `boot-report-20250623T204044Z` (provisioning,
  pre-NTP clock) plus two later reports timestamped after NTP sync — proof
  that `easymanet-boot-report` and `easymanet-management-lan` (with their 20s
  and 25s sleeps) finished their work.
- `ifstatus wan` showed DHCP up on `phy1-sta0` with a 192.168.1.0/24 lease;
  `iw dev phy1-sta0 link` reported `Connected to <bssid> ... SSID:
  <wifi-ssid> ... signal: -59 dBm`. Internet was reachable
  (`ping 1.1.1.1`, `nslookup google.com` both worked).

Useful one-liner from the Mac to confirm the gateway came up cleanly:

```sh
ssh root@10.41.254.1 'cat /etc/easymanet/provisioned; pgrep -a dropbear; \
  ls /boot/easymanet/; iw dev phy1-sta0 link | head -3; \
  ifstatus wan | grep -E "\"up\"|ipv4-address"'
```

If the boot-report list contains only entries with `reason=provisioned`, the
fix did not apply and the device is still in the reboot loop. Successful
boots show reports timestamped after NTP sync (current calendar date), not
just the build-date `20250623T...` stamp.
