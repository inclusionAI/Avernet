"""Backward-compatible import facade for ``clawevolve_plan.clawweb``.

Implementation lives in ``clawevolve_plan.integration.clawweb`` so domain logic is grouped
without breaking existing imports or script entrypoints.
"""

from .integration.clawweb import *  # noqa: F401,F403
