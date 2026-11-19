"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.errors``."""
import sys
from avernet_evolution.core.evolution_run import errors as _implementation

sys.modules[__name__] = _implementation
