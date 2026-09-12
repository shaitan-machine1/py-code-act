from __future__ import annotations

from py_code_act.agent.context import ContextFile, format_project_instructions
from py_code_act.tools.namespace import ToolRegistry
from py_code_act.tools.skills import SkillSummary, format_skills_for_prompt

BASE_PROMPT = (
    "You are a coding agent operating in a persistent Python kernel.\n\n"
    "Use normal prose for user-facing answers. To take an action, emit exactly one Python "
    "block whose opening and closing markers are each alone on a line:\n"
    "<exec>\n"
    "# Python code\n"
    "</exec>\n"
    "You may write prose before and after that block. Wait for the execution result before "
    "issuing more code. Never emit multiple execution blocks in one response.\n\n"
    "The Python namespace persists within one kernel generation. A restart or resumed session "
    "loses ordinary Python variables; filesystem and external side effects may persist. The "
    "injected `tools` object is available without import and can be inspected with dir() and "
    "help(). Execution output is returned completely and protocol-marker text in output is "
    "escaped. Malformed blocks and blocks from truncated model responses are not executed.\n"
)


def build_system_prompt(
    registry: ToolRegistry,
    skills: tuple[SkillSummary, ...],
    context_files: tuple[ContextFile, ...],
) -> str:
    sections = [BASE_PROMPT.rstrip()]
    tool_prompt = registry.prompt_contribution().strip()
    if tool_prompt:
        sections.append("Available harness services:\n" + tool_prompt)
    skill_prompt = format_skills_for_prompt(skills)
    if skill_prompt:
        sections.append(skill_prompt)
    project = format_project_instructions(context_files)
    if project:
        sections.append("Project instructions (nearer files are later):\n" + project)
    return "\n\n".join(sections) + "\n"
