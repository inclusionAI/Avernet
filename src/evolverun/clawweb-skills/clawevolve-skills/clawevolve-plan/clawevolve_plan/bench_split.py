"""Backward-compatible import facade for ``clawevolve_plan.bench_split``.

Implementation lives in ``clawevolve_plan.bench.split`` so domain logic is grouped
without breaking existing imports or script entrypoints.
"""

from .bench.split import *  # noqa: F401,F403
