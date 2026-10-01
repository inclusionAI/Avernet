"""Backward-compatible import facade for ``clawevolve_plan.artifact_publish``.

Implementation lives in ``clawevolve_plan.integration.artifact_publish`` so domain logic is grouped
without breaking existing imports or script entrypoints.
"""

from .integration.artifact_publish import *  # noqa: F401,F403
