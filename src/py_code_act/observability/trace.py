from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from py_code_act.config import TraceConfig
from py_code_act.domain.messages import utc_now
from py_code_act.storage.paths import ensure_private_file

from .redaction import redact


class TraceRecorder:
    """Best-effort, line-oriented development trace recorder."""

    def __init__(self, config: TraceConfig, session_id: str) -> None:
        self.enabled = config.enabled
        self.provider_raw = config.provider_raw
        self._sequence = 0
        self._reported_failure = False
        timestamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
        self.path = config.file or Path(f"/tmp/py-code-act/traces/{timestamp}-{session_id}.log")
        if self.enabled:
            try:
                ensure_private_file(self.path)
            except OSError as error:
                self.enabled = False
                self._report_failure(error)

    def _report_failure(self, error: BaseException) -> None:
        if not self._reported_failure:
            print(f"py-code-act: trace disabled: {error}", file=sys.stderr)
            self._reported_failure = True

    def record(self, event: str, payload: Any, **correlation: Any) -> None:
        if not self.enabled or (event == "openai.raw_event" and not self.provider_raw):
            return
        self._sequence += 1
        record = {
            "timestamp": utc_now(),
            "monotonic": time.monotonic(),
            "sequence": self._sequence,
            "event": event,
            "correlation": redact(correlation),
            "payload": redact(payload),
        }
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                stream.flush()
        except OSError as error:
            self.enabled = False
            self._report_failure(error)
