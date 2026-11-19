"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.models``."""
import sys
from avernet_evolution.core.evolution_strategy import models as _implementation

sys.modules[__name__] = _implementation
