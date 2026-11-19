"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_run.cron``."""
import sys
from avernet_evolution.core.evolution_run import cron as _implementation

sys.modules[__name__] = _implementation
