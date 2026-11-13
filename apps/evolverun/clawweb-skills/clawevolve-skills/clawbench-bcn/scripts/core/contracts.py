"""Internal Core → Runtime/Output contracts; implementations are selected in bench.py.

Runtime retains its handle (resource identifiers and ownership). prepare may partially
fail: quiesce and release must still be safe. submit is never blindly retried.
quiesce fixes the evidence boundary without deleting evidence. collect must identify
missing/truncated data. release only frees owned resources and must be repeat-safe.
Dictionaries retain existing Case/Judge fields; no extra object conversion layer.
"""
from pathlib import Path
from typing import Protocol


class Runtime(Protocol):
    metadata: dict

    def prepare(self) -> None: ...
    def submit(self, prompt: str, entry_member: str) -> dict: ...
    def poll(self, timeout: float) -> dict:
        """Return normalized status/output and raw session; no waiting loop here."""
        ...
    def quiesce(self, reason: str) -> dict:
        """Return evidence_stable plus optional local cleanup details."""
        ...
    def collect(self) -> dict:
        """Return messages, session, summary and optionally an AgentBench transcript list; archive originals through output."""
        ...
    def release(self) -> dict: ...


class Output(Protocol):
    run_id: str
    artifacts: Path  # Private staging directory for trusted code Judge.

    def progress(self, message: str) -> None: ...
    def write_json(self, name: str, data: object) -> None: ...
    def write_text(self, name: str, text: str) -> None: ...
    def save_result(self, info: dict, result: dict) -> None: ...
