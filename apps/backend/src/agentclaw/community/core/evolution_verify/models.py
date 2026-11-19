"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_verify.models``."""
import sys
from avernet_evolution.core.evolution_verify import models as _implementation

sys.modules[__name__] = _implementation
