"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.plugins.job_http``."""
import sys
from avernet_evolution.plugins import job_http as _implementation

sys.modules[__name__] = _implementation
