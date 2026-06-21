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
workflow builds the firmware image, generates checksums and a canonical
`easymanet-image-release.json`, publishes GitHub artifact attestations, signs
the manifest with Sigstore/cosign keyless signing, generates release notes, and
creates the GitHub Release when a release tag is supplied.

Stable tags use `images-vX.Y.Z`. Candidate tags use
`images-vX.Y.Z-candidate.N` and are published as GitHub prereleases. The
workflow keeps only the latest 5 stable image releases and the latest 3
candidate releases per target or candidates younger than 90 days, whichever
keeps fewer.

Official EasyMANET tooling treats schema-v2 manifests with the expected trust
assets as verified. Local or custom images remain checksum-only and
user-supplied.

Manual image releases can be started from the Actions tab with
`Image Release`.
