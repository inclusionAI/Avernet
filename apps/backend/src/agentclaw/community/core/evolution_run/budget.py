"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.budget``."""
import sys
from avernet_evolution.core.evolution_run import budget as _implementation

sys.modules[__name__] = _implementation
