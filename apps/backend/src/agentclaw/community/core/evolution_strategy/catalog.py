"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.catalog``."""
import sys
from avernet_evolution.core.evolution_strategy import catalog as _implementation

sys.modules[__name__] = _implementation
