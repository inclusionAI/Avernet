"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.store``."""
import sys
from avernet_evolution.core.evolution_run import store as _implementation

sys.modules[__name__] = _implementation
