"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.params_schema``."""
import sys
from avernet_evolution.core.evolution_strategy import params_schema as _implementation

sys.modules[__name__] = _implementation
