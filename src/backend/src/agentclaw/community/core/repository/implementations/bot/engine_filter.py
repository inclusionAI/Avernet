"""Engine-filter SQL criteria for the bot repository.

``aicoding`` is ``claude_code``'s internal runtime form (engine/form
vocabulary split), not an independently filterable engine value: a
``engine=aicoding`` filter must match both the legacy literal
``active_engine='aicoding'`` rows and the post-split ``claude_code`` rows
carrying a coding template. The SQL criterion mirrors
``workspace.runtime_identity.uses_aicoding_runtime`` arm-by-arm (legacy
short-circuit / form marker / non-empty non-``normalCC`` template): the
form-marker arm cannot be expressed on these columns (the marker lives in
the ``ac_templates.ext`` snapshot), but every form-marked row also matches
the template arm — creation writes the marker only for template-backed
bots. Parity with the predicate is pinned by
``test_bot_engine_form_filter``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_

_CODING_FORM_ENGINE = "aicoding"
_REAL_CODING_ENGINE = "claude_code"
_NEUTRAL_TEMPLATE_TYPE = "normalcc"


def engine_criterion(model: Any, engine: str) -> Any:
    """One SQLAlchemy criterion for an engine filter value, form-aware.

    ``aicoding`` expands to the union of both spellings; every other value
    keeps its historical exact-match semantics (``engine=claude_code`` stays
    the full claude_code population, aicoding form included — unchanged by
    design, matching the /bots/all expansion).
    """
    normalized = (engine or "").strip().lower().replace("-", "_")
    if normalized != _CODING_FORM_ENGINE:
        return model.active_engine == engine
    coding_template = and_(
        model.template_type.isnot(None),
        model.template_type != "",
        func.lower(model.template_type) != _NEUTRAL_TEMPLATE_TYPE,
    )
    return or_(
        model.active_engine == _CODING_FORM_ENGINE,
        and_(model.active_engine == _REAL_CODING_ENGINE, coding_template),
    )
