"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.host``."""
import sys
from avernet_evolution.core.evolution_run import host as _implementation

sys.modules[__name__] = _implementation
