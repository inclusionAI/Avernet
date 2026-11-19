"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.bindings_service``."""
import sys
from avernet_evolution.core.evolution_run import bindings_service as _implementation

sys.modules[__name__] = _implementation
