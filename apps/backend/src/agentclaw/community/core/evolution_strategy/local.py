"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.local``."""
import sys
from avernet_evolution.core.evolution_strategy import local as _implementation

sys.modules[__name__] = _implementation
