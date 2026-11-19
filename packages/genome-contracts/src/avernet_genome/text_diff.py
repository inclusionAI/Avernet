"""Genome v1 unified text diff encoding (not registry diff policy)."""

import difflib

_NO_NEWLINE = "\\ No newline at end of file"
_SENTINEL = "\n"  # a line from _split never contains one, so the suffix is unambiguous


def unified_diff(before: str, after: str) -> str:
    """The hunks of ``before`` → ``after`` as the contract's ``diff`` kind spells them (README §6.2).

    No ``---``/``+++`` header lines; ``\\ No newline at end of file`` where
    the last line of either side lacks one, so the text applies back
    through ``text_edits.apply_unified_diff``.
    """
    old, old_nl = _split(before)
    new, new_nl = _split(after)
    # A missing final newline is part of the last line's identity (README §6.3 "lines"): marking it
    # makes difflib treat a flip of that state as a changed line instead of an unchanged one, and the
    # marker comes out as the contract's "\ No newline at end of file" line.
    old_marked = old[:-1] + [old[-1] + _SENTINEL] if old and not old_nl else list(old)
    new_marked = new[:-1] + [new[-1] + _SENTINEL] if new and not new_nl else list(new)
    out: list[str] = []
    for line in list(difflib.unified_diff(old_marked, new_marked, lineterm="", n=3))[
        2:
    ]:
        if line.endswith(_SENTINEL) and not line.startswith("@@"):
            out.append(line[: -len(_SENTINEL)])
            out.append(_NO_NEWLINE)
        else:
            out.append(line)
    return "\n".join(out) + "\n" if out else ""


def _split(text: str) -> tuple[list[str], bool]:
    if text == "":
        return [], True
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
        return lines, True
    return lines, False
