"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.enums``."""
import sys
from avernet_evolution.core.evolution_run import enums as _implementation

sys.modules[__name__] = _implementation
