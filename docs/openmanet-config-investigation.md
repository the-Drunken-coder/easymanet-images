# OpenMANET Config Investigation

This document records what the OpenMANET web wizard modifies, based on
inspection of OpenMANET images and documentation.

> **Note**: This is based on OpenMANET's public documentation and
> source tree analysis. Exact UCI paths may vary by OpenMANET version.
> Update this document when testing against a specific OpenMANET build.

---

## Files Modified by OpenMANET Setup Wizard

### `/etc/config/wireless`

The wizard configures the mesh radio and optional local AP:

```conf
config wifi-device 'radio0'
    option type 'mac80211'
    option channel '42'
    option htmode 'HT20'
    option country 'US'
    option disabled '0'

config wifi-iface 'mesh0'
    option device 'radio2'
    option ifname 'wlan0'
    option network 'mesh'
    option mode 'mesh'
    option mesh_id '<mesh-id>'
    option encryption 'sae'
    option key '<mesh-password>'
    option mesh_fwding '0'

config wifi-iface 'ap0'          # Only if local AP enabled
    option device 'radio0'
    option network 'lan'
    option mode 'ap'
    option ssid '<ap-ssid>'
    option encryption 'sae'
    option key '<ap-password>'
```

### `/etc/config/network`

OpenMANET uses BATMAN-adv over the 802.11s HaLow interface. The
802.11s interface is a BATMAN hard interface; node IPs belong on
`bat0`, not directly on `wlan0`:

```conf
config interface 'bat0'
    option proto 'batadv'
    option routing_algo 'BATMAN_V'
    option bridge_loop_avoidance '1'
    option distributed_arp_table '1'
    option multicast_mode '1'
    option gw_mode '<server|client>'

config interface 'mesh'
    option proto 'batadv_hardif'
    option master 'bat0'

config interface 'meship'
    option proto 'static'
    option device 'bat0'
    option ipaddr '<node-ip>'
    option netmask '255.255.0.0'

config interface 'wan'           # Only on gate nodes
    option proto 'dhcp'
    option ifname 'eth0'
```

### `/etc/config/system`

Hostname and timezone:

```conf
config system
    option hostname '<hostname>'
    option timezone 'UTC'
```

### `/etc/config/mesh11sd`

`mesh11sd` manages the Morse 802.11s parameters. The tested working
state keeps Morse mesh forwarding disabled at the 802.11s layer and
lets BATMAN-adv carry the mesh:

```conf
config mesh11sd 'setup'
    option enabled '1'

config mesh11sd 'mesh_params'
    option mesh_fwding '0'
    option mesh_max_peer_links '10'
    option mesh_rssi_threshold '0'
    option mesh_hwmp_rootmode '0'
    option mesh_gate_announcements '<1-on-gate-0-on-point>'

config mesh11sd 'mesh_dynamic_peering'
    option enabled '1'

config mesh11sd 'mesh_beaconless'
    option mesh_beacon_less_mode '0'

config mesh11sd 'mbca'
    option mbca_config '1'
```

Do not set `dot11MeshHWMPRootMode=1`; it caused
`wpa_supplicant_s1g` parse failures on the tested image.

### `/etc/config/dhcp`

Mesh IP interface is excluded from DHCP serving:

```conf
config dhcp 'meship'
    option interface 'meship'
    option ignore '1'
```

### `/etc/config/firewall`

Mesh zone (open between mesh nodes):

```conf
config zone
    option name 'mesh'
    option network 'meship'
    option input 'ACCEPT'
    option output 'ACCEPT'
    option forward 'ACCEPT'
```

### `/etc/dropbear/authorized_keys`

SSH public keys for root login (Dropbear format).

### `/etc/shadow`

Root password hash is updated.

### `/etc/openmanetd/config.yml`

OpenMANET daemon configuration (if the daemon is present):

```yaml
mesh:
  id: "<mesh-id>"
  password: "<mesh-password>"
  channel: <channel>
  bandwidth_mhz: <bandwidth>
  country: "<country>"
node:
  name: "<node-name>"
  hostname: "<hostname>"
  role: "<gate|point>"
  ip: "<node-ip>"
```

---

## Setup-Complete Flags

OpenMANET may use one or more of these flags:

- `/etc/openmanet/setup-complete`
- `/etc/config/openmanet` with a `setup_complete` option
- A marker in the OpenMANET database (typically SQLite at
  `/var/lib/openmanet/openmanet.db`)

EasyMANET uses its own marker at `/etc/easymanet/provisioned` to
avoid conflicts and ensure idempotent provisioning.

---

## Service Control

After configuration changes, the following services need restart:

```text
/etc/init.d/network restart
/etc/init.d/mesh11sd enable
/etc/init.d/mesh11sd restart
/etc/init.d/openmanetd restart    # If daemon model
```

Or enable for subsequent boots:

```text
/etc/init.d/network enable
/etc/init.d/mesh11sd enable
/etc/init.d/openmanetd enable
```

---

## Restart Behavior

EasyMANET's current `provision.sh` does not call `reboot`. It applies
UCI changes, restarts networking, writes boot diagnostics, and leaves
any later power-cycle to the operator if a specific OpenMANET build or
field test requires one.

---

## Mesh Role Representation

| Role | UCI Changes | Network Behavior |
|------|------------|------------------|
| **gate** | BATMAN gateway mode `server`, mesh gate announcements enabled, WAN or management LAN preserved | Routes mesh traffic to uplink (internet/other network) |
| **point** | BATMAN gateway mode `client`, mesh gate announcements disabled, no WAN | Participates in mesh, no external routing |

---

## Known Gaps

The following sections need validation against a running OpenMANET
node to confirm exact UCI paths and service names:

1. Exact `/etc/config/wireless` radio numbering may vary by image.
   EasyMANET detects the Morse radio by `type='morse'` or
   `hwmode='11ah'`.
2. Exact encryption type may vary by OpenMANET version. The tested
   working OpenMANET path uses SAE plus the shared mesh password.
3. OpenMANET daemon path (`/etc/init.d/openmanetd` — may differ).
4. BATMAN-adv package naming and `batctl` availability by image.

**These should be verified by inspecting a node configured via the
OpenMANET web wizard.**
