"""Independent semantic witness for structured, non-JSON model output."""

import ast
import io
import json
import tokenize

from engine.community.core.dws_hosting.results import parse_decision
from engine.community.kernel.dws_execution import ExecutionError, HostingDecision


def structured_candidate(text: str, rule_version: int) -> HostingDecision:
    """Accept a complete flat literal, never execute expressions or select a substring."""
    try:
        source = text.strip()
        lines = source.splitlines()
        if lines and lines[0].lower() in ("```", "```json"):
            source = "\n".join(lines[1:]).strip()
            if source.endswith("```"):
                source = source[:-3].rstrip()
        if not source.startswith("{") or not source.endswith("}"):
            raise ValueError("complete object required")
        if any(t.type == tokenize.COMMENT for t in tokenize.generate_tokens(io.StringIO(source).readline)):
            raise ValueError("comments may contain competing instructions")
        node = ast.parse(source, mode="eval").body
        if not isinstance(node, ast.Dict):
            raise ValueError("single object required")
        fields = {}
        for key, value in zip(node.keys, node.values):
            if (
                not isinstance(key, ast.Constant) or type(key.value) is not str
                or key.value in fields or not isinstance(value, ast.Constant)
                or type(value.value) not in (str, int)
            ):
                raise ValueError("unique flat literal fields required")
            # Adjacent string literals are legal Python, but not an unambiguous
            # formatting change to a model decision. Require one token per scalar.
            for scalar in (key, value):
                tokens = [
                    t for t in tokenize.generate_tokens(
                        io.StringIO(ast.get_source_segment(source, scalar)).readline
                    )
                    if t.type not in (tokenize.NEWLINE, tokenize.ENDMARKER)
                ]
                if len(tokens) != 1 or tokens[0].type not in (tokenize.STRING, tokenize.NUMBER):
                    raise ValueError("one scalar token required")
                if type(scalar.value) is int and tokens[0].string != str(scalar.value):
                    raise ValueError("decimal integer required")
            fields[key.value] = value.value
        return parse_decision(json.dumps({"payloads": [{"text": json.dumps(fields)}]}), rule_version)
    except (ValueError, SyntaxError, TypeError, tokenize.TokenError, RecursionError):
        raise ExecutionError("format_repair_unsupported") from None
