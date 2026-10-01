from __future__ import annotations

import hashlib

from ..models import Diagnosis
from ..utils import slugify


def case_id(diag: Diagnosis) -> str:
    """Return a stable case id for diagnose -> plan handoff artifacts."""

    digest = hashlib.sha1(
        (diag.session.session_id + "\n" + diag.query).encode("utf-8", errors="ignore")
    ).hexdigest()[:10]
    session_slug = slugify(diag.session.session_id, 12)
    problem_slug = slugify(diag.common_problem_key, 32)
    return f"task_session_{session_slug}_{digest}_{problem_slug}"
