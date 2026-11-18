"""Per-method plan generators used by :class:`TaskPlanService`.

Each method has its own module so downstream edits stay local. Methods A and
C are KGBase-only (no LLM); methods B and D depend on the LLM — the Avernet PR
landing method B/D is the responsibility of A5; in this commit these two
modules expose stub functions returning an empty plan with
``reason="not_implemented_in_a4"`` so :meth:`TaskPlanService.generate_task_plans`
has a complete four-method assemble + ranking path.
"""

from __future__ import annotations

__all__: list[str] = []
