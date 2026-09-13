"""Backward-compatible import facade for ``clawevolve_plan.renderer``.

Implementation lives in ``clawevolve_plan.spec.renderer`` so domain logic is grouped
without breaking existing imports or script entrypoints.
"""

from .spec.renderer import *  # noqa: F401,F403
