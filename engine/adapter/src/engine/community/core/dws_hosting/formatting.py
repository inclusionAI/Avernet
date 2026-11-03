"""Lossless syntax repair only. Never infer a missing decision or reply text."""

import json

from engine.community.core.dws_hosting.results import parse_decision, strict_json
from engine.community.kernel.dws_execution import ExecutionError, HostingDecision


def repair_format(result_json: str, rule_version: int) -> HostingDecision:
    """Accept one flat JSON object with missing/trailing commas, not prose/multiple outputs.

    Keys and values are still decoded by the JSON parser. Only delimiters can
    change: no new key, action, rule version or text is generated. More general
    LLM repair remains disabled until the plugin is qualified in deployment.
    """
    try:
        result = strict_json(result_json)
        payloads = result["payloads"]
        if len(payloads) != 1 or set(payloads[0]) != {"text"}:
            raise ValueError("single plain payload required")
        text = payloads[0]["text"].strip()
        lines = text.splitlines()
        if (
            len(lines) >= 3
            and lines[0].lower() in ("```", "```json")
            and lines[-1] == "```"
        ):
            text = "\n".join(lines[1:-1]).strip()
        if not text.startswith("{") or not text.endswith("}"):
            raise ValueError("object required")
        decoder, fields, position = json.JSONDecoder(), {}, 1
        while position < len(text) - 1:
            while position < len(text) - 1 and text[position].isspace():
                position += 1
            if position == len(text) - 1:
                break
            key, position = decoder.raw_decode(text, position)
            if not isinstance(key, str) or key in fields:
                raise ValueError("unique field required")
            while position < len(text) - 1 and text[position].isspace():
                position += 1
            if text[position] != ":":
                raise ValueError("colon required")
            position += 1
            while position < len(text) - 1 and text[position].isspace():
                position += 1
            value, position = decoder.raw_decode(text, position)
            if type(value) not in (str, int):
                raise ValueError("flat scalar required")
            fields[key] = value
            while position < len(text) - 1 and text[position].isspace():
                position += 1
            if position < len(text) - 1 and text[position] == ",":
                position += 1
        # Reuse all original meta/error checks and the strict action/version contract.
        result["payloads"] = [{"text": json.dumps(fields, ensure_ascii=False)}]
        return parse_decision(json.dumps(result, ensure_ascii=False), rule_version)
    except (ValueError, KeyError, TypeError, IndexError, RecursionError):
        raise ExecutionError("invalid_decision") from None
