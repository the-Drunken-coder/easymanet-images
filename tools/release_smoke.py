#!/usr/bin/env python3
"""Build and smoke-test an installed EasyMANET release wheel."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
import warnings
from pathlib import Path


REMOVED_IMPORTS = (
    "easymanet.cli",
    "easymanet.cli_image",
    "easymanet.build",
    "easymanet.cli_flash",
    "easymanet.cli_common",
)
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
ELECTRON_SMOKE_TIMEOUT_SECONDS = 60


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    temp_root = Path(args.temp_root).resolve() if args.temp_root else None

    if temp_root:
        temp_root.mkdir(parents=True, exist_ok=True)
        return run_smoke(repo_root, temp_root, args)

    with tempfile.TemporaryDirectory(prefix="easymanet-release-smoke-") as tmp:
        return run_smoke(repo_root, Path(tmp), args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=Path(__file__).resolve().parents[1],
        help="Repository root to build from.",
    )
    parser.add_argument(
        "--temp-root",
        help="Temporary work directory. Defaults to a new temp directory.",
    )
    parser.add_argument(
        "--wheel",
        help="Existing EasyMANET wheel to install instead of building one.",
    )
    parser.add_argument(
        "--system-site-packages",
        action="store_true",
        help="Create the venv with access to this Python's site packages.",
    )
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Install the wheel without resolving dependencies.",
    )
    parser.add_argument(
        "--skip-electron",
        action="store_true",
        help="Skip the Electron smoke test.",
    )
    return parser.parse_args()


def run_smoke(repo_root: Path, temp_root: Path, args: argparse.Namespace) -> int:
    wheel = Path(args.wheel).resolve() if args.wheel else build_wheel(repo_root, temp_root)
    venv_dir = temp_root / "venv"
    workspace = temp_root / "workspace"
    fleet_source = repo_root / "examples" / "three-node-field-mesh.yml"

    create_venv(venv_dir, system_site_packages=args.system_site_packages)
    venv_python = venv_python_path(venv_dir)
    install_wheel(venv_python, wheel, no_deps=args.no_deps)

    env = os.environ.copy()
    env.update(
        {
            "EASYMANET_WORKSPACE": str(workspace),
            "EASYMANET_SKIP_UPDATE_CHECK": "1",
        }
    )
    easymanet = console_script(venv_dir, "easymanet")

    run([str(easymanet), "init"], env=env)
    fleets_dir = workspace / "Fleets"
    fleets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fleet_source, fleets_dir / "field.yml")
    run([str(easymanet), "fleets"], env=env)
    run([str(easymanet), "validate", "--config", "field", "--node", "point01"], env=env)
    verify_removed_imports(venv_python)

    if not args.skip_electron:
        run_electron_smoke(repo_root, venv_python, env)

    print("Release smoke passed.")
    return 0


def build_wheel(repo_root: Path, temp_root: Path) -> Path:
    clean_build_metadata(repo_root)
    wheelhouse = temp_root / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
            str(repo_root),
        ]
    )
    wheels = built_wheels(wheelhouse, repo_root)
    if len(wheels) != 1:
        pattern = wheel_glob_pattern(repo_root)
        raise SystemExit(
            f"Expected one wheel matching {pattern} in {wheelhouse}, found {len(wheels)}"
        )
    return wheels[0]


def built_wheels(wheelhouse: Path, repo_root: Path) -> list[Path]:
    return sorted(wheelhouse.glob(wheel_glob_pattern(repo_root)))


def wheel_glob_pattern(repo_root: Path) -> str:
    name = project_name(repo_root)
    normalized = re.sub(r"[-_.]+", "_", name).lower()
    return f"{normalized}-*.whl"


def project_name(repo_root: Path) -> str:
    pyproject = repo_root / "pyproject.toml"
    match = re.search(r'^name = "([^"]+)"$', pyproject.read_text(), re.MULTILINE)
    if not match:
        raise SystemExit(f"Could not find project name in {pyproject}")
    return match.group(1)


def clean_build_metadata(repo_root: Path) -> None:
    paths = [repo_root / "build", *repo_root.glob("*.egg-info")]
    found = [path for path in paths if path.exists()]
    if found:
        found_text = ", ".join(str(path.relative_to(repo_root)) for path in found)
        warnings.warn(
            f"clean_build_metadata removing stale build metadata: {found_text}",
            stacklevel=2,
        )
    for path in found:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            warnings.warn(f"clean_build_metadata could not remove {path}: {exc}", stacklevel=2)
            raise


def create_venv(venv_dir: Path, *, system_site_packages: bool) -> None:
    builder = venv.EnvBuilder(with_pip=True, system_site_packages=system_site_packages)
    builder.create(venv_dir)


def venv_python_path(venv_dir: Path) -> Path:
    return console_script(venv_dir, "python")


def console_script(venv_dir: Path, name: str) -> Path:
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv_dir / bin_dir / f"{name}{suffix}"


def install_wheel(venv_python: Path, wheel: Path, *, no_deps: bool) -> None:
    command = [str(venv_python), "-m", "pip", "install"]
    if no_deps:
        command.append("--no-deps")
    command.append(str(wheel))
    run(command)


def verify_removed_imports(venv_python: Path) -> None:
    code = (
        "import importlib\n"
        f"removed = {REMOVED_IMPORTS!r}\n"
        "for name in removed:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except ModuleNotFoundError:\n"
        "        pass\n"
        "    else:\n"
        "        raise SystemExit(f'{name} is still importable')\n"
    )
    run([str(venv_python), "-c", code])


def run_electron_smoke(repo_root: Path, venv_python: Path, env: dict[str, str]) -> None:
    electron_env = env.copy()
    electron_env.update(
        {
            "EASYMANET_PYTHON": str(venv_python),
            "EASYMANET_ELECTRON_NO_SOURCE_PATHS": "1",
            "EASYMANET_ELECTRON_SMOKE": "1",
        }
    )
    result = run(
        ["npm", "--prefix", str(repo_root / "apps" / "desktop" / "electron"), "start"],
        env=electron_env,
        capture=True,
        timeout=ELECTRON_SMOKE_TIMEOUT_SECONDS,
    )
    if '"selectedFleet":"field.yml"' not in result.stdout:
        raise SystemExit(f"Electron smoke did not select field.yml:\n{result.stdout}")


def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    capture: bool = False,
    timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command))
    try:
        result = subprocess.run(
            command,
            check=False,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        raise SystemExit(
            f"Command timed out after {timeout}s: {' '.join(command)}"
        ) from exc
    if not capture and result.stdout:
        print(result.stdout, end="")
    if not capture and result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        if capture:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
