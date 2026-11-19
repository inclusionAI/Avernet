"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.job_service``."""
import sys
from avernet_evolution.core.evolution_run import job_service as _implementation

sys.modules[__name__] = _implementation
