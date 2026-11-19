"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_verify.policy``."""
import sys
from avernet_evolution.core.evolution_verify import policy as _implementation

sys.modules[__name__] = _implementation
