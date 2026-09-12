from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from py_code_act.domain.messages import AssistantBlock, ExecBlock, TextBlock

ParserState = Literal["TEXT", "EXEC", "AFTER_EXEC", "ERROR"]


@dataclass(frozen=True, slots=True)
class ParserDelta:
    kind: Literal["text", "code"]
    delta: str


@dataclass(frozen=True, slots=True)
class ParserResult:
    blocks: tuple[AssistantBlock, ...]
    exec_block: ExecBlock | None
    error: str | None
    final_deltas: tuple[ParserDelta, ...] = ()


class ExecParser:
    """Incrementally parse one line-anchored ``<exec>`` block."""

    def __init__(self) -> None:
        self.state: ParserState = "TEXT"
        self._pending = ""
        self._raw: list[str] = []
        self._prefix: list[str] = []
        self._code: list[str] = []
        self._suffix: list[str] = []
        self._error: str | None = None
        self._finalized = False

    @property
    def error(self) -> str | None:
        return self._error

    def feed(self, chunk: str) -> tuple[ParserDelta, ...]:
        if self._finalized:
            raise RuntimeError("parser is already finalized")
        if not chunk:
            return ()
        self._raw.append(chunk)
        if self.state == "ERROR":
            return ()
        self._pending += chunk
        deltas: list[ParserDelta] = []
        while True:
            newline = self._pending.find("\n")
            if newline < 0:
                break
            line = self._pending[: newline + 1]
            self._pending = self._pending[newline + 1 :]
            delta = self._process_line(line)
            if delta is not None:
                deltas.append(delta)
        return tuple(deltas)

    @staticmethod
    def _line_value(line: str) -> str:
        return line.removesuffix("\n").removesuffix("\r")

    def _fail(self, message: str) -> None:
        self.state = "ERROR"
        self._error = message

    def _process_line(self, line: str) -> ParserDelta | None:
        value = self._line_value(line)
        if self.state == "TEXT":
            if value == "<exec>":
                self.state = "EXEC"
                return None
            if value == "</exec>":
                self._fail("closing </exec> marker without an opening marker")
                return None
            self._prefix.append(line)
            return ParserDelta("text", line)
        if self.state == "EXEC":
            if value == "<exec>":
                self._fail("a second <exec> marker is not allowed")
                return None
            if value == "</exec>":
                self.state = "AFTER_EXEC"
                return None
            self._code.append(line)
            return ParserDelta("code", line)
        if self.state == "AFTER_EXEC":
            if value in ("<exec>", "</exec>"):
                self._fail("only one execution block is allowed per assistant response")
                return None
            self._suffix.append(line)
            return ParserDelta("text", line)
        return None

    def finalize(self) -> ParserResult:
        if self._finalized:
            raise RuntimeError("parser is already finalized")
        self._finalized = True
        final_deltas: tuple[ParserDelta, ...] = ()
        if self.state != "ERROR" and self._pending:
            delta = self._process_line(self._pending)
            if delta is not None:
                final_deltas = (delta,)
            self._pending = ""
        if self.state == "EXEC":
            self._fail("assistant response ended inside an execution block")
        if self.state == "ERROR":
            return ParserResult((TextBlock("".join(self._raw)),), None, self._error, final_deltas)

        blocks: list[AssistantBlock] = []
        prefix = "".join(self._prefix)
        suffix = "".join(self._suffix)
        if prefix:
            blocks.append(TextBlock(prefix))
        exec_block: ExecBlock | None = None
        if self.state == "AFTER_EXEC":
            exec_block = ExecBlock("".join(self._code))
            blocks.append(exec_block)
            if suffix:
                blocks.append(TextBlock(suffix))
        elif not blocks:
            blocks.append(TextBlock(""))
        return ParserResult(tuple(blocks), exec_block, None, final_deltas)
