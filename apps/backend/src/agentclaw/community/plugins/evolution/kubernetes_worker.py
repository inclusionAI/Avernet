"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.plugins.kubernetes_worker``."""
import sys
from avernet_evolution.plugins import kubernetes_worker as _implementation

sys.modules[__name__] = _implementation
