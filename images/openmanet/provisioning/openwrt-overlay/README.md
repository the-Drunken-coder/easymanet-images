# OpenWrt Overlay

Copy the contents of this directory into an OpenMANET/OpenWrt firmware
tree under `files/` before building the image.

These files install the generic EasyMANET first-boot hooks:

- `etc/uci-defaults/99-easymanet`
- `etc/uci-defaults/95-easymanet-display-status`
- `usr/lib/easymanet/provision.sh`
- `usr/lib/easymanet/provision-runtime.sh`
- `usr/lib/easymanet/provision-lib.sh`
- `usr/lib/easymanet/api.sh`
- `usr/lib/easymanet/api-lib.sh`
- `usr/lib/easymanet/status-lib.sh`
- `usr/lib/easymanet/display-status.sh`

At flash time, EasyMANET writes the node-specific payload to the FAT
boot partition at:

- `/easymanet/provision.json`

On first boot, `provision.sh` copies that file into overlay storage at
`/etc/easymanet/provision.json`, applies configuration, and marks the
node provisioned.
