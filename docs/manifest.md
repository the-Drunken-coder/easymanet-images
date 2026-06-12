# Config Manifest Reference

Complete reference for every field in `fleet.yml`.

## Top-level Fields

### `version` (required, integer)

Config schema version. Currently must be `1`.

```yaml
version: 1
```

---

## `mesh` (required, object)

Mesh-wide settings applied to every node.

### `mesh.id` (required, string)

Mesh network identifier. Used as the 802.11s mesh ID.

```yaml
mesh:
  id: my-mesh-network
```

### `mesh.password` (required, string)

Mesh network password/key. Used for SAE (WPA3) encryption.

```yaml
mesh:
  password: "strong-mesh-password"
```

### `mesh.channel` (required, integer)

WiFi channel for the mesh radio. Valid values depend on the country
regulatory domain. For the tested `rpi4-mm6108-spi` MM6108 target in the US,
use channel `42` with `mesh.bandwidth_mhz: 2`.

```yaml
mesh:
  channel: 42
```

### `mesh.bandwidth_mhz` (required, integer)

Channel bandwidth in MHz. Must be one of: 1, 2, 4, 8.
For the tested `rpi4-mm6108-spi` MM6108 target in the US, use `2`.

```yaml
mesh:
  bandwidth_mhz: 2
```

### `mesh.country` (required, string)

Two-letter ISO country code for WiFi regulatory compliance.

```yaml
mesh:
  country: US
```

---

## `defaults` (required, object)

Default values inherited by all nodes unless overridden.

### `defaults.target` (required, string)

Target hardware platform. Currently only `rpi4-mm6108-spi` is supported.

```yaml
defaults:
  target: rpi4-mm6108-spi
```

### `defaults.local_ap` (object)

Default local access point settings.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Whether to create a local WiFi AP |
| `password` | string | — | AP password (min 8 chars when enabled) |
| `ssid` | string | `{nodename}-local` | AP SSID (override per node) |

```yaml
defaults:
  local_ap:
    enabled: true
    password: "ap-password-here"
```

### `defaults.management` (object)

Node management settings.

| Field | Type | Description |
|-------|------|-------------|
| `root_password_hash` | string | Hashed root password (from `openssl passwd -6`) |
| `ssh_authorized_keys` | list[string] | SSH public keys for root login (installed one per line via jsonfilter on the node) |

SSH enable/disable is **not** set in `fleet.yml`. Use `easymanet flash
--enable-ssh` or `--disable-ssh` (see [flashing.md](flashing.md)). The
flash command may write `management.ssh_enabled` into the boot-partition
(when `--enable-ssh` or `--disable-ssh` is used; otherwise first boot uses the role default)
`provision.json`.

```yaml
defaults:
  management:
    root_password_hash: "$6$salt$hash..."
    ssh_authorized_keys:
      - "ssh-ed25519 AAAAC3..."
```

### `defaults.gateway` (object)

Default gateway settings for gate nodes.

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | Whether gateway mode is enabled |
| `uplink_interface` | string | Uplink network interface name. `eth0` is reserved for wired management on `br-lan`; use Wi-Fi or a separate interface for WAN routing. |
| `wifi` | object | Optional Wi-Fi uplink settings. Defaults can hold SSID/password while a gate node enables them with `gateway.wifi.enabled: true`. |

### `defaults.role` (string)

Default node role. Must be `gate` or `point`.

---

## `nodes` (required, object)

Map of node names to their specific configurations. Each key is the
node name used with `--node` in CLI commands.

### Node Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `role` | string | yes (or from defaults) | `point` | `gate` or `point` |
| `hostname` | string | yes | node name | System hostname |
| `ip` | string | yes | — | Static node IP on the BATMAN mesh interface (`bat0`) |
| `target` | string | no | from defaults | Hardware target |
| `local_ap` | object | no | from defaults | Local AP override |
| `gateway` | object | no | from defaults | Gateway settings override |

### Node `local_ap` Overrides

Any field in `defaults.local_ap` can be overridden per node:

```yaml
nodes:
  manet01:
    local_ap:
      ssid: manet01-local
      password: "different-ap-password"
```

### Node `gateway` Overrides

```yaml
nodes:
  manet01:
    role: gate
    gateway:
      enabled: true
      uplink_interface: wifi
      wifi:
        enabled: true
```

With `uplink_interface: wifi`, EasyMANET joins the configured upstream Wi-Fi as
`wan`. If SSH is enabled at flash time, the node opens SSH on that WAN zone so
the desktop Mesh tab can discover it on the operator LAN. With
`uplink_interface: eth0`, EasyMANET leaves `eth0` on `br-lan` for wired
management and does not run WAN DHCP on that management bridge.

---

## Resolved Config

The `easymanet render` command outputs the fully resolved config
after merging mesh settings, defaults, and node overrides.

Priority (highest to lowest):
1. Node-specific values
2. `defaults` section values
3. `mesh` section values (mesh-wide, not overridable per node)

## Validation Rules

| Rule | Error Level |
|------|-------------|
| version must be 1 | Error |
| mesh.id is required | Error |
| mesh.password is required | Error |
| mesh.channel is required | Error |
| mesh.bandwidth_mhz must be 1, 2, 4, or 8 | Error |
| mesh.country is required | Error |
| nodes section must have at least one node | Error |
| Node names must be unique (case-insensitive) | Error |
| Hostnames must be unique | Error |
| IP addresses must be unique and valid | Error |
| role must be gate or point | Error |
| target must be one of the supported targets (e.g., rpi4-mm6108-spi) | Error |
| local_ap.password min 8 chars when enabled | Error |
| Selected node must exist in manifest | Error |
| Invalid SSH key format | Error |
| No SSH keys provided | Warning |
| root_password_hash is empty | Warning |
| Gate role without uplink_interface | Warning |
| mesh.country must be two-letter ISO code (e.g. US) | Error |
| gateway.wifi.enabled requires ssid and password | Error |
| gateway.wifi.encryption must be psk2, sae, none, psk, or psk-mixed | Error |

## Security

- Empty `root_password_hash` does not set a root password on the node.
- `gateway.uplink_interface: eth0` is reserved for wired management on
  `br-lan`; use a separate uplink or Wi-Fi uplink for WAN routing.
- `gateway.wifi.enabled` with SSH enabled opens SSH on the WAN firewall zone.
- Mesh credentials may be written to `/etc/openmanetd/config.yml` in plaintext
  when that file exists on the image.
