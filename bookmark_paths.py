"""Runtime path resolution for local bookmark data."""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "X_BOOKMARKS_HOME"


def resolve_base_dir() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd().resolve()
