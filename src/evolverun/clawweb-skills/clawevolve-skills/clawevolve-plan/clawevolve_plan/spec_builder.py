"""Backward-compatible import facade for ``clawevolve_plan.spec_builder``.

Implementation lives in ``clawevolve_plan.spec.builder`` so domain logic is grouped
without breaking existing imports or script entrypoints.
"""

from .spec.builder import *  # noqa: F401,F403
