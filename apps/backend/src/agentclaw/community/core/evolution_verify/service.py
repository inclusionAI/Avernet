"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_verify.service``."""
import sys
from avernet_evolution.core.evolution_verify import service as _implementation

sys.modules[__name__] = _implementation
