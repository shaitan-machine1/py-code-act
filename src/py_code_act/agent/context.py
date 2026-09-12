from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

CONTEXT_CANDIDATES = (
    "AGENTS.override.md",
    "AGENTS.md",
    "AGENTS.MD",
    "CLAUDE.md",
    "CLAUDE.MD",
)


@dataclass(frozen=True, slots=True)
class ContextFile:
    path: Path
    content: str


def ancestor_chain(cwd: Path) -> tuple[Path, ...]:
    """Return filesystem ancestors in root-to-working-directory order."""

    resolved = cwd.resolve()
    return tuple(reversed((resolved, *resolved.parents)))


def discover_context_files(cwd: Path, global_file: Path | None = None) -> list[ContextFile]:
    """Load one Pi-compatible instruction file per directory, nearest last."""

    files: list[ContextFile] = []
    seen: set[Path] = set()
    if global_file is not None and global_file.is_file():
        resolved = global_file.resolve()
        files.append(ContextFile(resolved, resolved.read_text(encoding="utf-8")))
        seen.add(resolved)

    for directory in ancestor_chain(cwd):
        for name in CONTEXT_CANDIDATES:
            candidate = directory / name
            if candidate.is_file():
                resolved = candidate.resolve()
                if resolved not in seen:
                    files.append(ContextFile(resolved, resolved.read_text(encoding="utf-8")))
                    seen.add(resolved)
                break
    return files


def format_project_instructions(files: Iterable[ContextFile]) -> str:
    sections = [
        (
            f'<project_instructions path="{item.path}">\n'
            f"{item.content.rstrip()}\n</project_instructions>"
        )
        for item in files
    ]
    if not sections:
        return ""
    return "\n\n".join(sections)
