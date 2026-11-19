"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.api``."""
import sys
from avernet_evolution.core.evolution_run import api as _implementation

sys.modules[__name__] = _implementation
