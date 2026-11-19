"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.errors``."""
import sys
from avernet_evolution.core.evolution_strategy import errors as _implementation

sys.modules[__name__] = _implementation
