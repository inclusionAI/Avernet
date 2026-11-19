"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.container_host``."""
import sys
from avernet_evolution.core.evolution_run import container_host as _implementation

sys.modules[__name__] = _implementation
