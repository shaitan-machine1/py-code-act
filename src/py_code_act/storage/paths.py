from __future__ import annotations

import os
from pathlib import Path


def ensure_private_directory(path: Path) -> Path:
    """Create a directory tree with user-only permissions where supported."""

    missing: list[Path] = []
    current = path
    while not current.exists() and current.parent != current:
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in reversed(missing):
        try:
            directory.chmod(0o700)
        except OSError:
            pass
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def ensure_private_file(path: Path) -> Path:
    """Create a file and restrict it to the current user where supported."""

    ensure_private_directory(path.parent)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
