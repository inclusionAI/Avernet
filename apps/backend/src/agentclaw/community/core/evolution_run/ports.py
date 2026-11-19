"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.ports``."""
import sys
from avernet_evolution.core.evolution_run import ports as _implementation

sys.modules[__name__] = _implementation
