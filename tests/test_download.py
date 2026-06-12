"""Tests for cached image selection."""

import gzip
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from easymanet import download


def _write_gzip(path, payload=b"image-bytes", corrupt=False, trailing=b""):
    with gzip.open(path, "wb") as f:
        f.write(payload)
    if corrupt:
        path.write_bytes(path.read_bytes()[:-8])
    if trailing:
        with path.open("ab") as f:
            f.write(trailing)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def image_cache(tmp_path, monkeypatch):
    cache = tmp_path / "images"
    cache.mkdir()
    manifest = tmp_path / "images.json"
    version_file = tmp_path / "version.json"
    monkeypatch.setattr(download, "cache_dir", lambda: cache)
    monkeypatch.setattr(download, "images_manifest_path", lambda: manifest)
    monkeypatch.setattr(download, "version_file_path", lambda: version_file)
    return SimpleNamespace(cache=cache, manifest=manifest, version_file=version_file)


class BytesResponse:
    def __init__(self, body: bytes, headers=None):
        self.headers = headers or {"Content-Length": str(len(body))}
        self.stream = io.BytesIO(body)

    def read(self, size=-1):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _part_files(cache: Path) -> list[Path]:
    return [path for path in cache.iterdir() if path.name.endswith(".part")]


def test_get_cached_image_skips_empty_img(image_cache):
    image = image_cache.cache / "openmanet-test-rpi4-mm6108-spi.img"
    image.touch()

    assert download.get_cached_image("rpi4-mm6108-spi") is None
    assert download._valid_cached_image(image) is False


def test_get_cached_image_skips_corrupt_configured_image(image_cache):
    image = image_cache.cache / "openmanet-test-rpi4-mm6108-spi.img.gz"
    _write_gzip(image, corrupt=True)

    image_cache.manifest.write_text(json.dumps({
        "rpi4-mm6108-spi": {
            "url": f"https://example.invalid/{image.name}",
            "version": "test",
        }
    }))

    assert download.get_cached_image("rpi4-mm6108-spi") is None


def test_get_cached_image_returns_valid_matching_image(image_cache):
    image = image_cache.cache / "openmanet-test-rpi4-mm6108-spi.img.gz"
    _write_gzip(image)

    image_cache.manifest.write_text(json.dumps({
        "rpi4-mm6108-spi": {
            "url": f"https://example.invalid/{image.name}",
            "version": "test",
            "sha256": _sha256(image),
        }
    }))

    assert download.get_cached_image("rpi4-mm6108-spi") == image


def test_get_cached_image_allows_openwrt_trailing_metadata(image_cache):
    image = image_cache.cache / "openmanet-test-rpi4-mm6108-spi.img.gz"
    _write_gzip(image, trailing=b'{"metadata": "openwrt sysupgrade trailer"}')
    image_cache.manifest.write_text(json.dumps({
        "rpi4-mm6108-spi": {
            "url": f"https://example.invalid/{image.name}",
            "version": "test",
            "sha256": _sha256(image),
        }
    }))

    assert download.get_cached_image("rpi4-mm6108-spi") == image


def test_get_cached_image_ignores_file_removed_during_sort(image_cache, monkeypatch):
    missing = image_cache.cache / "openmanet-old-rpi4-mm6108-spi.img"
    valid = image_cache.cache / "openmanet-new-rpi4-mm6108-spi.img.gz"
    missing.write_bytes(b"old")
    _write_gzip(valid)

    original_stat = Path.stat

    def flaky_stat(path, *args, **kwargs):
        if path == missing:
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    assert download.get_cached_image("rpi4-mm6108-spi", sha256=_sha256(valid)) == valid


def test_get_cached_image_requires_checksum(image_cache):
    image = image_cache.cache / "openmanet-test-rpi4-mm6108-spi.img.gz"
    _write_gzip(image)

    image_cache.manifest.write_text(json.dumps({
        "rpi4-mm6108-spi": {
            "url": f"https://example.invalid/{image.name}",
            "version": "test",
        }
    }))

    assert download.get_cached_image("rpi4-mm6108-spi") is None


def test_check_latest_version_defaults_to_easymanet_images_repo(image_cache, monkeypatch):
    repos = []

    def fake_check(repo, target):
        repos.append((repo, target))
        return download.ImageRef("1.6.5", "https://example.invalid/image.img.gz", "a" * 64)

    monkeypatch.setattr(download, "_check_github_release", fake_check)

    ref = download.check_latest_version("rpi4-mm6108-spi")

    assert ref is not None
    assert repos == [(download.DEFAULT_IMAGE_GITHUB_REPO, "rpi4-mm6108-spi")]


def test_get_cached_image_rejects_checksum_mismatch(image_cache):
    image = image_cache.cache / "openmanet-test-rpi4-mm6108-spi.img.gz"
    _write_gzip(image)

    image_cache.manifest.write_text(json.dumps({
        "rpi4-mm6108-spi": {
            "url": f"https://example.invalid/{image.name}",
            "version": "test",
            "sha256": "0" * 64,
        }
    }))

    assert download.get_cached_image("rpi4-mm6108-spi") is None


def test_download_paths_resolve_workspace_after_import(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"

    monkeypatch.setenv("EASYMANET_WORKSPACE", str(first))
    assert download.cache_dir() == first / "Images"

    monkeypatch.setenv("EASYMANET_WORKSPACE", str(second))
    assert download.cache_dir() == second / "Images"
    assert download.images_manifest_path() == second / "Images" / "images.json"
    assert download.version_file_path() == second / "Images" / "version.json"


def test_download_image_rejects_non_https_url(tmp_path, monkeypatch):
    monkeypatch.setattr(download, "cache_dir", lambda: tmp_path / "images")
    monkeypatch.setattr(download, "version_file_path", lambda: tmp_path / "version.json")

    with pytest.raises(OSError, match="Unsupported image URL scheme"):
        download.download_image("rpi4-mm6108-spi", "test", "file:///etc/passwd", "0" * 64)

    with pytest.raises(OSError, match="HTTPS"):
        download.download_image(
            "rpi4-mm6108-spi",
            "test",
            "http://example.invalid/image.img.gz",
            "0" * 64,
        )


def test_download_image_verifies_sha256(tmp_path, monkeypatch):
    payload = b"firmware-bytes"
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb") as f:
        f.write(payload)
    body = compressed.getvalue()
    expected = hashlib.sha256(body).hexdigest()

    monkeypatch.setattr(download, "cache_dir", lambda: tmp_path / "images")
    monkeypatch.setattr(download, "version_file_path", lambda: tmp_path / "version.json")
    monkeypatch.setattr(download.urllib.request, "urlopen", lambda *_a, **_k: BytesResponse(body))
    events = []

    path = download.download_image(
        "rpi4-mm6108-spi",
        "test",
        "https://example.invalid/openmanet-test-rpi4-mm6108-spi.img.gz",
        expected,
        emit=events.append,
    )

    assert path.read_bytes() == body
    assert _part_files(path.parent) == []
    assert [event["type"] for event in events] == [
        "download_started",
        "download_url",
        "download_progress",
        "download_completed",
    ]
    assert events[2]["downloaded_bytes"] == len(body)
    assert events[2]["total_bytes"] == len(body)
    assert events[2]["percent"] == 100
    version_data = json.loads((tmp_path / "version.json").read_text())
    assert version_data["rpi4-mm6108-spi"] == {
        "version": "test",
        "sha256": expected,
        "url": "https://example.invalid/openmanet-test-rpi4-mm6108-spi.img.gz",
    }


def test_download_image_records_metadata_when_reusing_existing_cache(tmp_path, monkeypatch):
    cache = tmp_path / "images"
    cache.mkdir()
    image = cache / "openmanet-test-rpi4-mm6108-spi.img.gz"
    _write_gzip(image)
    expected = _sha256(image)

    monkeypatch.setattr(download, "cache_dir", lambda: cache)
    monkeypatch.setattr(download, "version_file_path", lambda: tmp_path / "version.json")

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("cached image should avoid network")

    monkeypatch.setattr(download.urllib.request, "urlopen", fail_urlopen)

    path = download.download_image(
        "rpi4-mm6108-spi",
        "test-cache",
        "https://example.invalid/openmanet-test-rpi4-mm6108-spi.img.gz",
        expected,
    )

    assert path == image
    version_data = json.loads((tmp_path / "version.json").read_text())
    assert version_data["rpi4-mm6108-spi"] == {
        "version": "test-cache",
        "sha256": expected,
        "url": "https://example.invalid/openmanet-test-rpi4-mm6108-spi.img.gz",
    }


def test_download_image_removes_file_on_sha256_mismatch(tmp_path, monkeypatch):
    payload = b"firmware-bytes"
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb") as f:
        f.write(payload)
    body = compressed.getvalue()

    monkeypatch.setattr(download, "cache_dir", lambda: tmp_path / "images")
    monkeypatch.setattr(download, "version_file_path", lambda: tmp_path / "version.json")
    monkeypatch.setattr(download.urllib.request, "urlopen", lambda *_a, **_k: BytesResponse(body))

    with pytest.raises(OSError, match="SHA-256 mismatch"):
        download.download_image(
            "rpi4-mm6108-spi",
            "test",
            "https://example.invalid/openmanet-test-rpi4-mm6108-spi.img.gz",
            "0" * 64,
        )

    assert not (tmp_path / "images" / "openmanet-test-rpi4-mm6108-spi.img.gz").exists()
    assert _part_files(tmp_path / "images") == []


def test_download_image_removes_part_file_on_stream_error(tmp_path, monkeypatch):
    class FailingResponse:
        headers = {"Content-Length": "10"}

        def read(self, size=-1):
            del size
            raise OSError("disk full")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    cache = tmp_path / "images"
    monkeypatch.setattr(download, "cache_dir", lambda: cache)
    monkeypatch.setattr(download, "version_file_path", lambda: tmp_path / "version.json")
    monkeypatch.setattr(download.urllib.request, "urlopen", lambda *_a, **_k: FailingResponse())

    with pytest.raises(OSError, match="disk full"):
        download.download_image(
            "rpi4-mm6108-spi",
            "test",
            "https://example.invalid/openmanet-test-rpi4-mm6108-spi.img.gz",
            "0" * 64,
        )

    assert not (cache / "openmanet-test-rpi4-mm6108-spi.img.gz").exists()
    assert _part_files(cache) == []


def test_download_image_preserves_existing_cache_when_force_download_fails(tmp_path, monkeypatch):
    existing = io.BytesIO()
    with gzip.GzipFile(fileobj=existing, mode="wb") as f:
        f.write(b"existing-firmware")
    existing_body = existing.getvalue()
    expected = hashlib.sha256(existing_body).hexdigest()

    class FailingResponse:
        headers = {"Content-Length": "10"}

        def read(self, size=-1):
            del size
            raise TimeoutError("temporary read timeout")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    cache = tmp_path / "images"
    cache.mkdir()
    dest = cache / "openmanet-test-rpi4-mm6108-spi.img.gz"
    dest.write_bytes(existing_body)
    monkeypatch.setattr(download, "cache_dir", lambda: cache)
    monkeypatch.setattr(download, "version_file_path", lambda: tmp_path / "version.json")
    monkeypatch.setattr(download.urllib.request, "urlopen", lambda *_a, **_k: FailingResponse())

    with pytest.raises(TimeoutError, match="temporary read timeout"):
        download.download_image(
            "rpi4-mm6108-spi",
            "test",
            "https://example.invalid/openmanet-test-rpi4-mm6108-spi.img.gz",
            expected,
            force=True,
        )

    assert dest.read_bytes() == existing_body
    assert _part_files(cache) == []


def test_pick_release_asset_falls_back_to_pattern_match():
    release = {
        "tag_name": "1.6.5",
        "assets": [
            {
                "name": "openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz",
                "browser_download_url": "https://example.com/fallback.img.gz",
            }
        ],
    }
    result = download._pick_release_asset(release, "rpi4-mm6108-spi")
    assert result is not None
    assert result.version == "1.6.5"
    assert result.url.endswith("fallback.img.gz")


def test_pick_release_asset_uses_fuzzy_match_when_exact_name_missing(capsys):
    release = {
        "tag_name": "2.0.0",
        "assets": [
            {
                "name": "custom-openmanet-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz",
                "browser_download_url": "https://example.com/custom.img.gz",
            }
        ],
    }
    result = download._pick_release_asset(release, "rpi4-mm6108-spi")
    assert result is not None
    assert result.version == "2.0.0"
    assert result.url == "https://example.com/custom.img.gz"
    assert "Using release asset" in capsys.readouterr().err


def test_pick_release_asset_uses_github_asset_digest():
    release = {
        "tag_name": "1.6.5",
        "assets": [
            {
                "name": "openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz",
                "browser_download_url": "https://example.com/image.img.gz",
                "digest": f"sha256:{'a' * 64}",
            }
        ],
    }

    result = download._pick_release_asset(release, "rpi4-mm6108-spi")

    assert result is not None
    assert result.version == "1.6.5"
    assert result.url == "https://example.com/image.img.gz"
    assert result.sha256 == "a" * 64


def test_image_ref_from_release_manifest_uses_release_tag_as_version():
    manifest = {
        "target": "rpi4-mm6108-spi",
        "openmanet_version": "1.6.5",
        "artifact": {
            "filename": "openmanet.img.gz",
            "sha256": "b" * 64,
        },
    }
    assets = [
        {
            "name": "openmanet.img.gz",
            "browser_download_url": "https://example.com/openmanet.img.gz",
        }
    ]

    result = download._image_ref_from_release_manifest(
        manifest,
        assets,
        "rpi4-mm6108-spi",
        release_version="images-v0.2.0",
    )

    assert result is not None
    assert result.version == "images-v0.2.0"
    assert result.sha256 == "b" * 64


def test_fetch_github_release_retries_transient_urlopen_error(monkeypatch):
    payload = json.dumps({"tag_name": "v1.2.3"}).encode()
    calls = []

    def fake_urlopen(url, timeout=15):
        calls.append((url, timeout))
        if len(calls) == 1:
            raise download.urllib.error.URLError("temporary")
        return BytesResponse(payload)

    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(download.time, "sleep", lambda _seconds: None)

    assert download._fetch_github_release("org/repo") == {"tag_name": "v1.2.3"}
    assert len(calls) == 2


def test_fetch_checksum_text_retries_transient_timeout(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout=30):
        calls.append((url, timeout))
        if len(calls) == 1:
            raise TimeoutError("temporary")
        return BytesResponse(b"checksum text")

    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(download.time, "sleep", lambda _seconds: None)

    assert download._fetch_checksum_text("https://example.invalid/SHA256SUMS") == "checksum text"
    assert len(calls) == 2


def test_extract_sha256_from_checksum_text_matches_image_name():
    text = f"{'b' * 64}  openmanet.img.gz\n{'c' * 64}  other.img.gz\n"

    assert download._extract_sha256_from_checksum_text(text, "openmanet.img.gz") == "b" * 64


def test_extract_sha256_from_checksum_text_requires_exact_image_token():
    text = f"{'b' * 64}  not-openmanet.img.gz\n{'c' * 64}  other.img.gz\n"

    assert download._extract_sha256_from_checksum_text(text, "openmanet.img.gz") is None


def test_extract_sha256_from_checksum_text_accepts_star_prefixed_filename():
    text = f"{'b' * 64} *openmanet.img.gz\n"

    assert download._extract_sha256_from_checksum_text(text, "openmanet.img.gz") == "b" * 64


def test_extract_sha256_from_checksum_text_accepts_single_digest_file():
    text = f"{'b' * 64}\n"

    assert download._extract_sha256_from_checksum_text(text, "openmanet.img.gz") == "b" * 64


def test_extract_sha256_from_checksum_text_rejects_multi_digest_only_file():
    text = f"{'b' * 64}\n{'c' * 64}\n"

    assert download._extract_sha256_from_checksum_text(text, "openmanet.img.gz") is None


def test_check_latest_version_treats_invalid_configured_sha256_as_missing(
    tmp_path,
    monkeypatch,
    capsys,
):
    manifest = tmp_path / "images.json"
    manifest.write_text(json.dumps({
        "rpi4-mm6108-spi": {
            "url": "https://example.invalid/openmanet.img.gz",
            "version": "test",
            "sha256": "not-a-sha256",
        }
    }))
    monkeypatch.setattr(download, "images_manifest_path", lambda: manifest)

    result = download.check_latest_version("rpi4-mm6108-spi")

    assert result is not None
    assert result.url == "https://example.invalid/openmanet.img.gz"
    assert result.sha256 is None
    assert "invalid SHA-256 configured" in capsys.readouterr().err


def test_check_easymanet_update_respects_env_repo(monkeypatch):
    payload = json.dumps({"tag_name": "v9.9.9"}).encode()
    monkeypatch.setenv("EASYMANET_UPDATE_REPO", "org/custom-easymanet")

    def fake_urlopen(url, timeout=10):
        assert "org/custom-easymanet" in url
        class Resp:
            def read(self):
                return payload
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return Resp()

    monkeypatch.setattr(download, "__version__", "0.1.0")
    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)
    assert download.check_easymanet_update() == "9.9.9"


def test_check_easymanet_update_uses_default_repo(monkeypatch):
    payload = json.dumps({"tag_name": "v9.9.9"}).encode()

    def fake_urlopen(url, timeout=10):
        assert download.easymanet_update_repo() in url
        class Resp:
            def read(self):
                return payload
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        return Resp()

    monkeypatch.setattr(download, "__version__", "0.1.0")
    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)
    assert download.check_easymanet_update() == "9.9.9"
