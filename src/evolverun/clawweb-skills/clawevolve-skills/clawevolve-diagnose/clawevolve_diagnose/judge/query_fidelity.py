from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from ..models import SessionRow
from ..utils import assess_replayability, compact_replay_text


@dataclass(frozen=True)
class ReplayQueryDecision:
    """Result of enforcing the source session's real task-entry contract."""

    query: str
    source_invocation: str = ""
    overridden: bool = False

    @property
    def notes(self) -> list[str]:
        if not self.source_invocation:
            return []
        notes = ["source_invocation_type:slash_command"]
        notes.append(
            "replay_query_source:source_slash_command"
            if self.overridden
            else "source_slash_command_preserved"
        )
        return notes


class ReplayQueryFidelityPolicy:
    """Keep executable task-entry syntax when a session was started by it.

    Slash commands are routing contracts, not prose. Rewriting one into natural
    language can bypass the skill that the benchmark is intended to exercise.
    The policy therefore treats the first observed user slash command as the
    canonical replay query and lets LLMs generate only diagnosis and grading
    semantics around it.
    """

    def decide(self, row: SessionRow, candidate_query: str = "") -> ReplayQueryDecision:
        invocation = self.source_slash_command(row)
        candidate = compact_replay_text(candidate_query, max_len=900)
        if not invocation:
            return ReplayQueryDecision(query=candidate)
        return ReplayQueryDecision(
            query=invocation,
            source_invocation=invocation,
            overridden=candidate != invocation,
        )

    def source_slash_command(self, row: SessionRow) -> str:
        for value in self._source_user_texts(row):
            command = self._as_slash_command(value)
            if command:
                return command
        return ""

    def _source_user_texts(self, row: SessionRow) -> Iterable[str]:
        # first_question is already the parser's best representation of the
        # initiating user request and must win over later follow-up turns.
        if row.first_question:
            yield row.first_question
        for line in str(row.user_text or "").splitlines():
            if line.strip():
                yield line
        yield from self._raw_user_messages(row.raw_session)

    def _raw_user_messages(self, raw_session: Any) -> Iterable[str]:
        if not isinstance(raw_session, dict):
            return
        messages = raw_session.get("messages")
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except json.JSONDecodeError:
                return
        if not isinstance(messages, list):
            return
        for message in messages:
            if not isinstance(message, dict):
                continue
            nested = message.get("message") if isinstance(message.get("message"), dict) else {}
            role = str(
                message.get("role")
                or nested.get("role")
                or message.get("type")
                or message.get("speaker")
                or ""
            ).strip().lower()
            if role not in {"user", "human"}:
                continue
            yield self._text_value(message.get("content") or nested.get("content"))

    def _as_slash_command(self, value: str) -> str:
        compact = compact_replay_text(value, max_len=900)
        assessment = assess_replayability(compact)
        if assessment.query_type == "slash_command" and not assessment.hard_failures:
            return compact
        return ""

    def _text_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(
                self._text_value(item) for item in value if item is not None
            )
        if isinstance(value, dict):
            for key in ("text", "content", "message", "query", "prompt", "input"):
                if value.get(key) is not None:
                    return self._text_value(value[key])
        return ""
