"""Version information from pyproject.toml"""
from pathlib import Path
import tomllib

_version_cache: str | None = None


def get_version() -> str:
    """Read the version from pyproject.toml. Cached after first read."""
    global _version_cache
    if _version_cache is not None:
        return _version_cache

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
            _version_cache = data.get("project", {}).get("version", "unknown")
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        _version_cache = "unknown"

    return _version_cache
