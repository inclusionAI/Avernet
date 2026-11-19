"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.registration``."""
import sys
from avernet_evolution.core.evolution_strategy import registration as _implementation

sys.modules[__name__] = _implementation
