"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.scheduler``."""
import sys
from avernet_evolution.core.evolution_run import scheduler as _implementation

sys.modules[__name__] = _implementation
