from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import yaml

IGNORE_FILES = (".gitignore", ".ignore", ".fdignore")


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    description: str
    file_path: Path
    base_dir: Path
    disable_model_invocation: bool = False


@dataclass(frozen=True, slots=True)
class SkillContent:
    name: str
    content: str
    file_path: Path
    base_dir: Path


@dataclass(frozen=True, slots=True)
class SkillDiagnostic:
    level: str
    message: str
    path: Path


@dataclass(frozen=True, slots=True)
class SkillDiscovery:
    skills: tuple[SkillSummary, ...]
    diagnostics: tuple[SkillDiagnostic, ...]


def _parse_frontmatter(content: str) -> dict[str, Any] | None:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        raise ValueError("unterminated YAML frontmatter") from None
    loaded = yaml.safe_load("\n".join(lines[1:end]))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("skill frontmatter must be a mapping")
    return {str(key): value for key, value in loaded.items()}


def _validate_name(name: str) -> list[str]:
    errors: list[str] = []
    if len(name) > 64:
        errors.append(f"name exceeds 64 characters ({len(name)})")
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in name):
        errors.append("name must contain only lowercase a-z, 0-9, and hyphens")
    if name.startswith("-") or name.endswith("-"):
        errors.append("name must not start or end with a hyphen")
    if "--" in name:
        errors.append("name must not contain consecutive hyphens")
    return errors


def _load_file(path: Path) -> tuple[SkillSummary | None, list[SkillDiagnostic]]:
    diagnostics: list[SkillDiagnostic] = []
    try:
        content = path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(content)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
        if path.name == "SKILL.md":
            diagnostics.append(SkillDiagnostic("warning", str(error), path))
        return None, diagnostics
    if frontmatter is None:
        return None, diagnostics
    description_value = frontmatter.get("description")
    description = description_value if isinstance(description_value, str) else ""
    if not description.strip():
        if path.name == "SKILL.md":
            diagnostics.append(SkillDiagnostic("warning", "description is required", path))
        return None, diagnostics
    if len(description) > 1024:
        diagnostics.append(
            SkillDiagnostic(
                "warning", f"description exceeds 1024 characters ({len(description)})", path
            )
        )
    raw_name = frontmatter.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else path.parent.name
    diagnostics.extend(SkillDiagnostic("warning", error, path) for error in _validate_name(name))
    return (
        SkillSummary(
            name=name,
            description=description,
            file_path=path.resolve(),
            base_dir=path.parent.resolve(),
            disable_model_invocation=frontmatter.get("disable-model-invocation") is True,
        ),
        diagnostics,
    )


def _ignore_patterns(directory: Path) -> list[str]:
    patterns: list[str] = []
    for name in IGNORE_FILES:
        path = directory / name
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("!"):
                    patterns.append(stripped.removeprefix("/"))
        except OSError:
            pass
    return patterns


def _is_ignored(name: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(f"{name}/", pattern)
        for pattern in patterns
    )


def _scan_directory(
    directory: Path, *, include_root_markdown: bool
) -> tuple[list[SkillSummary], list[SkillDiagnostic]]:
    skills: list[SkillSummary] = []
    diagnostics: list[SkillDiagnostic] = []
    if not directory.is_dir():
        return skills, diagnostics

    def visit(current: Path, include_markdown: bool) -> None:
        patterns = _ignore_patterns(current)
        declared = current / "SKILL.md"
        if declared.is_file() and not _is_ignored(declared.name, patterns):
            skill, found = _load_file(declared)
            diagnostics.extend(found)
            if skill is not None:
                skills.append(skill)
            return
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError as error:
            diagnostics.append(SkillDiagnostic("warning", str(error), current))
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name == "node_modules":
                continue
            if _is_ignored(entry.name, patterns):
                continue
            if entry.is_dir():
                # Agent Skills compatibility directories ignore root Markdown files but
                # permit declared Markdown skills inside grouping directories.
                visit(entry, True)
            elif include_markdown and entry.suffix == ".md":
                skill, found = _load_file(entry)
                diagnostics.extend(found)
                if skill is not None:
                    skills.append(skill)

    visit(directory, include_root_markdown)
    return skills, diagnostics


def _project_agents_directories(cwd: Path) -> list[Path]:
    current = cwd.resolve()
    directories: list[Path] = []
    while True:
        directories.append(current / ".agents" / "skills")
        if (current / ".git").exists() or current.parent == current:
            break
        current = current.parent
    return directories


def discover_skills(cwd: Path, explicit_paths: tuple[Path, ...] = ()) -> SkillDiscovery:
    """Discover skills with deterministic first-source-wins precedence."""

    sources: list[tuple[Path, bool]] = []
    sources.extend((path.expanduser().resolve(), True) for path in explicit_paths)
    home = Path.home()
    sources.extend(
        (
            (home / ".pi" / "agent" / "skills", True),
            (home / ".agents" / "skills", False),
            (cwd.resolve() / ".pi" / "skills", True),
        )
    )
    sources.extend((path, False) for path in _project_agents_directories(cwd))

    selected: dict[str, SkillSummary] = {}
    canonical_paths: set[Path] = set()
    diagnostics: list[SkillDiagnostic] = []
    for path, include_root_markdown in sources:
        if not path.exists():
            if path in tuple(item.expanduser().resolve() for item in explicit_paths):
                diagnostics.append(SkillDiagnostic("warning", "skill path does not exist", path))
            continue
        if path.is_file():
            skill, found = _load_file(path)
            found_skills = [skill] if skill is not None else []
        else:
            found_skills, found = _scan_directory(path, include_root_markdown=include_root_markdown)
        diagnostics.extend(found)
        for skill in found_skills:
            canonical = skill.file_path.resolve()
            if canonical in canonical_paths:
                continue
            existing = selected.get(skill.name)
            if existing is not None:
                diagnostics.append(
                    SkillDiagnostic(
                        "collision",
                        f'name "{skill.name}" collision; keeping {existing.file_path}',
                        skill.file_path,
                    )
                )
                continue
            selected[skill.name] = skill
            canonical_paths.add(canonical)
    return SkillDiscovery(tuple(selected.values()), tuple(diagnostics))


class SkillsService:
    def __init__(self, skills: tuple[SkillSummary, ...]) -> None:
        self._skills = {skill.name: skill for skill in skills}

    def list(self) -> list[SkillSummary]:
        """Return discovered skill metadata in stable discovery order."""

        return list(self._skills.values())

    def load(self, name: str) -> SkillContent:
        """Load a skill's full instructions and relative-path base directory."""

        try:
            skill = self._skills[name]
        except KeyError:
            raise KeyError(f"unknown skill: {name}") from None
        return SkillContent(
            name=skill.name,
            content=skill.file_path.read_text(encoding="utf-8"),
            file_path=skill.file_path,
            base_dir=skill.base_dir,
        )


def format_skills_for_prompt(skills: tuple[SkillSummary, ...]) -> str:
    visible = [skill for skill in skills if not skill.disable_model_invocation]
    if not visible:
        return ""
    lines = [
        "The following skills provide specialized instructions for specific tasks.",
        "Load a matching skill with tools.skills.load(name) before following it.",
        "Resolve relative paths against the returned base directory.",
        "",
        "<available_skills>",
    ]
    replacements = {'"': "&quot;", "'": "&apos;"}
    for skill in visible:
        lines.extend(
            (
                "  <skill>",
                f"    <name>{escape(skill.name, replacements)}</name>",
                f"    <description>{escape(skill.description, replacements)}</description>",
                f"    <location>{escape(str(skill.file_path), replacements)}</location>",
                "  </skill>",
            )
        )
    lines.append("</available_skills>")
    return "\n".join(lines)
