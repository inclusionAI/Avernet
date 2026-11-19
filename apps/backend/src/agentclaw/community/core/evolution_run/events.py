"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.events``."""
import sys
from avernet_evolution.core.evolution_run import events as _implementation

sys.modules[__name__] = _implementation
