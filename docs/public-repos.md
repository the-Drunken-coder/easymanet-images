# Public Product Repositories

EasyMANET now treats the authoring repository as the source of truth and the
public product repositories as generated release surfaces.

## Repositories

- `the-Drunken-coder/easymanet-images`: firmware image build, verification,
  manifest, and release artifacts.
- `the-Drunken-coder/easymanet-cli`: installable CLI and automation surface.
- `the-Drunken-coder/easymanet-desktop`: local-first desktop operator-console
  surface. This is release plumbing plus product direction until app code lands.

## Local Publish Preview

Generate all public repo contents without touching GitHub:

```bash
python scripts/publish_product_repos.py --product all
```

The generated trees are written to `build/product-repos/`.

## Publishing

The publish script can also create missing public repositories, push generated
contents, and dispatch the public bootstrap workflows:

```bash
python scripts/publish_product_repos.py \
  --product all \
  --create-missing \
  --push
```

Release dispatches are intentional:

```bash
python scripts/publish_product_repos.py \
  --product images \
  --dispatch \
  --release-tag images-v0.1.0 \
  --openmanet-version 1.6.5 \
  --board ekh-bcm2711 \
  --target rpi4-mm6108-spi \
  --jobs 2
```

For GitHub Actions publishing, set `EASYMANET_PUBLIC_REPO_TOKEN` to a narrowly
scoped credential that can write to the public product repos and trigger
`repository_dispatch`. Prefer a GitHub App or fine-grained token over a broad
personal token.

## CI Shape

Each generated public repo has:

- a normal `CI` workflow for lightweight checks,
- a tiny `bootstrap-release.yml` workflow that accepts `repository_dispatch`
  and invokes the larger release workflow,
- a product release workflow that owns the artifact work for that repo.

This keeps public runner cost on the public product repo while the authoring
repo controls the generated release logic.
