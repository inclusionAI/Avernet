"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.protocols``."""
import sys
from avernet_evolution.core.evolution_strategy import protocols as _implementation

sys.modules[__name__] = _implementation
