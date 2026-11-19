"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.registry``."""
import sys
from avernet_evolution.core.evolution_strategy import registry as _implementation

sys.modules[__name__] = _implementation
