"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.run_service``."""
import sys
from avernet_evolution.core.evolution_run import run_service as _implementation

sys.modules[__name__] = _implementation
