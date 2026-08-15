"""User-global OpenWiki state under ``~/.openwiki/`` (roadmap Phase 3):

- **`config.toml`** — cross-project defaults (``[models]``/``[build]``/``[graph]``),
  slotting into setting resolution *below* a project's manifest and *above* the
  built-in defaults: ``flag > manifest > ~/.openwiki/config.toml > default``.
- **`registry.toml`** — named projects + an ``active`` pointer, so you can
  ``openwiki project use <name>`` and switch from anywhere. Location always wins:
  the registry's active project is only a fallback when you are not inside one.

The home directory can be overridden with ``$OPENWIKI_HOME`` (checked at call time,
so tests can point it at a temp dir).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.10
    import tomli as _toml  # type: ignore[no-redef]

CONFIG_FILE = "config.toml"
REGISTRY_FILE = "registry.toml"


def home_dir(home=None) -> Path:
    return Path(home or os.environ.get("OPENWIKI_HOME") or (Path.home() / ".openwiki"))


def _quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


class UserConfig:
    """``~/.openwiki/config.toml`` — cross-project setting defaults."""

    def __init__(self, data: Optional[dict] = None) -> None:
        self.data = data or {}

    @classmethod
    def load(cls, home=None) -> "UserConfig":
        path = home_dir(home) / CONFIG_FILE
        if path.is_file():
            try:
                with path.open("rb") as fh:
                    return cls(_toml.load(fh))
            except (ValueError, OSError):
                pass
        return cls({})

    def section(self, name: str) -> dict:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def setting(self, section: str, key: str, default=None):
        value = self.section(section).get(key)
        return default if value is None else value


class Registry:
    """``~/.openwiki/registry.toml`` — named projects + the active pointer."""

    def __init__(self, path: Path, data: Optional[dict] = None) -> None:
        self.path = path
        self.data = data or {"projects": {}}

    @classmethod
    def load(cls, home=None) -> "Registry":
        path = home_dir(home) / REGISTRY_FILE
        if path.is_file():
            try:
                with path.open("rb") as fh:
                    return cls(path, _toml.load(fh))
            except (ValueError, OSError):
                pass
        return cls(path, {"projects": {}})

    def projects(self) -> dict:
        value = self.data.get("projects", {})
        return dict(value) if isinstance(value, dict) else {}

    def active(self) -> Optional[str]:
        return self.data.get("active")

    def active_path(self) -> Optional[Path]:
        name = self.active()
        projects = self.projects()
        return Path(projects[name]) if name and name in projects else None

    def add(self, name: str, root) -> None:
        self.data.setdefault("projects", {})[name] = Path(root).as_posix()
        self.save()

    def remove(self, name: str) -> bool:
        projects = self.data.setdefault("projects", {})
        if name not in projects:
            return False
        projects.pop(name)
        if self.data.get("active") == name:
            self.data.pop("active", None)
        self.save()
        return True

    def use(self, name: str) -> bool:
        if name not in self.projects():
            return False
        self.data["active"] = name
        self.save()
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if self.data.get("active"):
            lines.append(f"active = {_quote(self.data['active'])}")
        lines.append("")
        lines.append("[projects]")
        for name, path in self.projects().items():
            lines.append(f"{_quote(name)} = {_quote(path)}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
