"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.operation_service``."""
import sys
from avernet_evolution.core.evolution_run import operation_service as _implementation

sys.modules[__name__] = _implementation
