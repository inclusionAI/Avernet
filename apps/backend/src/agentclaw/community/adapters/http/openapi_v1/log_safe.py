"""Rendering caller-supplied values for a log line, bounded and escaped.

One helper, in its own module for one reason: both ends of the caller-identity
seam need it and they cannot import each other. ``principal.py`` depends on
``admission.py``, so the formatter cannot live in the former; and "how to render
a rejected id" is not admission policy, so it should not live in the latter.

**Every refusal on this surface logs values the caller chose.** That is not
incidental — the response carries a fixed message precisely so it discloses
nothing, which makes the log the only record of *which* user or bot was asked
for. Writing those values raw hands the party being refused two things:

- **A line-injection.** An id containing a newline appends convincing extra
  lines to the log, so the audit trail of refusals can be written by the person
  being refused.
- **An amplifier.** ``user_id`` and ``owner_id`` are deliberately declared with
  ``min_length=1`` and **no upper bound** (see ``principal.py`` for why: a cap
  there would lock out a caller whose credential the gateway accepts). Nothing
  else stops one request from writing a hundred kilobytes of log.

``repr`` handles the first — it escapes newlines and control characters — and
the length cap handles the second. The same discipline the 422 handler and
``error_logging.format_call_params`` already follow.
"""

from __future__ import annotations

#: How much of a rejected value reaches the log. Long enough to identify a
#: misconfigured partner integration, short enough that a caller cannot choose
#: how many bytes each refusal costs.
LOGGED_VALUE_LIMIT = 128


def for_log(value: str) -> str:
    """A caller-supplied value as one escaped, bounded token.

    The overflow marker carries the true length, so an operator can still tell a
    500-character id from a 500-kilobyte one without the line carrying either.
    """
    if len(value) <= LOGGED_VALUE_LIMIT:
        return repr(value)
    return f"{value[:LOGGED_VALUE_LIMIT]!r}…(+{len(value) - LOGGED_VALUE_LIMIT})"


__all__ = ["LOGGED_VALUE_LIMIT", "for_log"]
