"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.strategies.correction_fixer``."""
import sys
from avernet_evolution.core.evolution_strategy.strategies import correction_fixer as _implementation

sys.modules[__name__] = _implementation
