"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.conformance``."""
import sys
from avernet_evolution.core.evolution_strategy import conformance as _implementation

sys.modules[__name__] = _implementation
