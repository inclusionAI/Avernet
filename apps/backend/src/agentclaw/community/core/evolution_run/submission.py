"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.submission``."""
import sys
from avernet_evolution.core.evolution_run import submission as _implementation

sys.modules[__name__] = _implementation
