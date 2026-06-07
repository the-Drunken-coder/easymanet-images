# EasyMANET Images

Public firmware image factory for EasyMANET-flavored OpenMANET releases.

This repository is generated from `the-Drunken-coder/easymanet`. Its job is to
build, verify, and publish flashable OpenMANET images with the EasyMANET
provisioning overlay included.

## What Lives Here

- OpenMANET image build automation.
- The EasyMANET provisioning overlay.
- Image release workflows.
- Image checksums and release manifests.

The shared CLI, provisioning logic, and tests are copied from the authoring repo
so the image pipeline builds from the same behavior that users flash with.

## Release Flow

The tiny bootstrap workflow accepts an intentional `repository_dispatch` or
manual trigger, then invokes the larger image release workflow. The release
workflow builds the firmware image, generates checksums and a manifest, uploads
the build artifact, and creates a GitHub Release when a release tag is supplied.

Manual image releases can be started from the Actions tab with
`Image Release`.
