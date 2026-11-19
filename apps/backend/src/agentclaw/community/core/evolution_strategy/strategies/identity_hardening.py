"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_strategy.strategies.identity_hardening``."""
import sys
from avernet_evolution.core.evolution_strategy.strategies import identity_hardening as _implementation

sys.modules[__name__] = _implementation
