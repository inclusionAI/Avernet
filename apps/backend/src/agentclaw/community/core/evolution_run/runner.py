"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.runner``."""
import sys
from avernet_evolution.core.evolution_run import runner as _implementation

sys.modules[__name__] = _implementation
