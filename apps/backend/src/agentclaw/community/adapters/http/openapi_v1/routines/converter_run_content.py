"""Allowlisted legacy execution content for the public routine history contract."""

from .schemas import RoutineRunMessage


def text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def run_content(data: dict) -> dict:
    messages = []
    for field in ("messages", "conversation", "turns"):
        rows = data.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            content = next((value for key in ("content", "text", "output")
                            if (value := text(row.get(key))) and value.strip()), None)
            if content is not None:
                messages.append(RoutineRunMessage(role=text(row.get("role")) or "assistant", content=content))
        if messages:
            break
    return {"input": text(data.get("input")), "output": text(data.get("output")),
            "error": text(data.get("error")), "messages": messages}
