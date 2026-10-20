"""The rewrite measure of the Bot Genome contract.

The contract (``apps/backend/api-contracts/genome/v1/README.md`` §6.3) flags a
patch's change to a text file as a ``rewrite`` when more than 2/5 of its lines
changed, by exact line LCS, bounded so that every implementation stays cheap and
reaches the same answer. ``rewrite-vectors.json`` is this module's oracle.

The exact LCS is computed with Myers' O((N+M)D) shortest edit script, where D
is the number of inserted plus deleted lines: LCS = (N + M - D) / 2. Myers is
fast when the files are close, but D can approach N + M when they are not, so
it stops once its work would pass N * M and finishes with the O(N * M) table
instead. Either way the work stays within twice the cell product the contract
bounds. Any exact algorithm gives the same answer.

Usage::

    rewrite_flagged(["a", "b"], ["a", "c"], numerator=2, denominator=5, cell_limit=2**24)  # -> True
"""
from __future__ import annotations

from collections.abc import Sequence


def to_lines(text: str) -> list[str]:
    """README §6.3: split on "\n"; a final "\n" ends the last line, and empty text has no lines."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def strip_common(a: Sequence[str], b: Sequence[str]) -> tuple[int, Sequence[str], Sequence[str]]:
    """Remove the common prefix, then the common suffix of what remains; return the count and the rest."""
    prefix = 0
    while prefix < len(a) and prefix < len(b) and a[prefix] == b[prefix]:
        prefix += 1
    a, b = a[prefix:], b[prefix:]
    suffix = 0
    while suffix < len(a) and suffix < len(b) and a[-1 - suffix] == b[-1 - suffix]:
        suffix += 1
    return prefix + suffix, a[: len(a) - suffix], b[: len(b) - suffix]


def lcs_length(a: Sequence[str], b: Sequence[str]) -> int:
    """Exact length of the longest common subsequence of two line lists, in O(N * M) work at most."""
    if len(b) > len(a):
        a, b = b, a  # LCS is symmetric; keep the table's row on the shorter side
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 0
    frontier = {1: 0}  # diagonal k -> furthest x reached with d edits
    for d in range(n + m + 1):
        if d * (n + m) > n * m:
            return _lcs_table(a, b)
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and frontier[k - 1] < frontier[k + 1]):
                x = frontier[k + 1]  # step down: insert a line of b
            else:
                x = frontier[k - 1] + 1  # step right: delete a line of a
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x, y = x + 1, y + 1
            frontier[k] = x
            if x >= n and y >= m:
                return (n + m - d) // 2
    raise AssertionError("unreachable: d = n + m always reaches the end")


def _lcs_table(a: Sequence[str], b: Sequence[str]) -> int:
    """The O(N * M) dynamic programme, one row of len(b) + 1 cells at a time (b is the shorter side)."""
    row = [0] * (len(b) + 1)
    for line in a:
        diagonal = 0
        for j, other in enumerate(b):
            above = row[j + 1]
            row[j + 1] = diagonal + 1 if line == other else max(above, row[j])
            diagonal = above
    return row[-1]


def rewrite_flagged(
    a: Sequence[str], b: Sequence[str], *, numerator: int, denominator: int, cell_limit: int
) -> bool:
    """README §6.3: True when the change from ``a`` to ``b`` is a rewrite."""
    total = len(a) + len(b)
    if total == 0:
        return False
    stripped, rest_a, rest_b = strip_common(a, b)
    if len(rest_a) * len(rest_b) > cell_limit:
        return True
    lcs = stripped + lcs_length(rest_a, rest_b)
    return denominator * (total - 2 * lcs) > numerator * total
