"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.bindings``."""
import sys
from avernet_evolution.core.evolution_run import bindings as _implementation

sys.modules[__name__] = _implementation
