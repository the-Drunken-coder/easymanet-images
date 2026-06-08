# OpenWrt Overlay

Copy the contents of this directory into an OpenMANET/OpenWrt firmware
tree under `files/` before building the image.

These files install the generic EasyMANET first-boot hooks:

- `etc/uci-defaults/99-easymanet`
- `usr/lib/easymanet/provision.sh`

At flash time, EasyMANET writes the node-specific payload to the FAT
boot partition at:

- `/easymanet/provision.json`

On first boot, `provision.sh` copies that file into overlay storage at
`/etc/easymanet/provision.json`, applies configuration, and marks the
node provisioned.
