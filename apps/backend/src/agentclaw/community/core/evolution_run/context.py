"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.context``."""
import sys
from avernet_evolution.core.evolution_run import context as _implementation

sys.modules[__name__] = _implementation
