"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_verify.enums``."""
import sys
from avernet_evolution.core.evolution_verify import enums as _implementation

sys.modules[__name__] = _implementation
