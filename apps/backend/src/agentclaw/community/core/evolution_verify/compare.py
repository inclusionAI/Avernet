"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_verify.compare``."""
import sys
from avernet_evolution.core.evolution_verify import compare as _implementation

sys.modules[__name__] = _implementation
