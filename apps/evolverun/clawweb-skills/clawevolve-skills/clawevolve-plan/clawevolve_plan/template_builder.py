"""Backward-compatible import facade for ``clawevolve_plan.template_builder``.

Implementation lives in ``clawevolve_plan.bench.template_builder`` so domain logic is grouped
without breaking existing imports or script entrypoints.
"""

from .bench.template_builder import *  # noqa: F401,F403
