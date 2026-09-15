"""Minimal explicit Plugin loading (doc section 9/17: "plugin_sdk/ # Plugin
계약·검증·로더", "설치 검증된 외부 plugins/ 경로를 유지한다"). Each plugin under the
top-level ``plugins/`` directory is its own importable top-level package (e.g.
``mightyzap_modbus``, not ``plugins.mightyzap_modbus``) -- this just makes that
directory importable; it does not scan or auto-discover plugins, since only
explicitly-configured Targets should ever be imported (doc: "실제 사용하는 Target만
생성한다").
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PLUGINS_DIR = _REPO_ROOT / "plugins"


def ensure_plugins_on_path(plugins_dir: Path = DEFAULT_PLUGINS_DIR) -> None:
    path_str = str(plugins_dir)
    if plugins_dir.is_dir() and path_str not in sys.path:
        sys.path.insert(0, path_str)
