"""The registry's rewrite measure against the contract's vectors (README §6.3)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentclaw.community.core.bot_genome.contract import risk_tiers
from agentclaw.community.core.bot_genome.rewrite import lcs_length, rewrite_flagged, strip_common, to_lines

_CONTRACT = Path(__file__).resolve().parents[4] / "api-contracts" / "genome" / "v1"
_VECTORS = json.loads((_CONTRACT / "rewrite-vectors.json").read_text(encoding="utf-8"))


def _expand(runs: list[list[Any]]) -> list[str]:
    return [line for line, count in runs for _ in range(count)]


@pytest.mark.parametrize("vector", _VECTORS["vectors"], ids=lambda v: v["name"])
def test_rewrite_vectors(vector: dict[str, Any]) -> None:
    threshold = risk_tiers()["rewrite_threshold"]
    given, expected = vector["input"], vector["expected"]
    if "a_text" in given:
        a, b = to_lines(given["a_text"]), to_lines(given["b_text"])
    else:
        a, b = _expand(given["a"]), _expand(given["b"])
    _, rest_a, rest_b = strip_common(a, b)
    assert (len(rest_a) * len(rest_b) > threshold["lcs_cell_limit"]) == expected["over_cell_limit"]
    if "lcs" in expected:
        assert len(a) - len(rest_a) + lcs_length(rest_a, rest_b) == expected["lcs"]
    flagged = rewrite_flagged(
        a, b, numerator=threshold["numerator"], denominator=threshold["denominator"],
        cell_limit=threshold["lcs_cell_limit"],
    )
    assert flagged == expected["flagged"], vector["note"]


@pytest.mark.parametrize(("text", "lines"), [("", []), ("\n", [""]), ("a", ["a"]), ("a\n", ["a"]), ("a\r\nb", ["a\r", "b"])])
def test_to_lines(text: str, lines: list[str]) -> None:
    assert to_lines(text) == lines
