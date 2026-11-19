"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.job_wire``."""
import sys
from avernet_evolution.core.evolution_run import job_wire as _implementation

sys.modules[__name__] = _implementation
