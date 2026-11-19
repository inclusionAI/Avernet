"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.records``."""
import sys
from avernet_evolution.core.evolution_run import records as _implementation

sys.modules[__name__] = _implementation
