"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.models_helpers``."""
import sys
from avernet_evolution.core.evolution_run import models_helpers as _implementation

sys.modules[__name__] = _implementation
