"""YAML fleet manifest parser.

Parses fleet.yml, validates basic structure, and provides access
to mesh settings, defaults, and node definitions.
"""

from pathlib import Path
from typing import Any, Dict, List

import yaml


class ManifestError(Exception):
    pass


class Manifest:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            raise ManifestError(f"Config file not found: {self.path}")
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except OSError as e:
            raise ManifestError(f"Could not read config file {self.path}: {e}") from e
        except yaml.YAMLError as e:
            raise ManifestError(f"Invalid YAML in {self.path}: {e}") from e
        self.data = self._validate_structure(raw)

    def _validate_structure(self, raw: Any) -> Dict[str, Any]:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ManifestError(
                f"Manifest root must be a mapping, got {type(raw).__name__}"
            )
        for section in ("mesh", "defaults", "nodes"):
            value = raw.get(section)
            if value is not None and not isinstance(value, dict):
                raise ManifestError(
                    f"Manifest section '{section}' must be a mapping, "
                    f"got {type(value).__name__}"
                )
        nodes = raw.get("nodes", {})
        if isinstance(nodes, dict):
            for name, node in nodes.items():
                if not isinstance(node, dict):
                    raise ManifestError(
                        f"Manifest node '{name}' must be a mapping, "
                        f"got {type(node).__name__}"
                    )
        return raw

    @property
    def version(self) -> int:
        return self.data.get("version", 0)

    @property
    def mesh(self) -> Dict[str, Any]:
        return self.data.get("mesh", {})

    @property
    def defaults(self) -> Dict[str, Any]:
        return self.data.get("defaults", {})

    @property
    def nodes(self) -> Dict[str, Any]:
        return self.data.get("nodes", {})

    def get_node(self, name: str) -> Dict[str, Any]:
        if name not in self.nodes:
            raise ManifestError(f"Node '{name}' not found in manifest")
        return self.nodes[name]

    def get_default(self, key: str, default: Any = None) -> Any:
        return self.defaults.get(key, default)

    def get_mesh(self, key: str, default: Any = None) -> Any:
        return self.mesh.get(key, default)

    def node_names(self) -> List[str]:
        return list(self.nodes.keys())


def load_manifest(path: str) -> Manifest:
    return Manifest(path)
