"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.worker``."""
import sys
from avernet_evolution.core.evolution_run import worker as _implementation

sys.modules[__name__] = _implementation
