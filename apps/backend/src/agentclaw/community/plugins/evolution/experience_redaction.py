"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.plugins.experience_redaction``."""
import sys
from avernet_evolution.plugins import experience_redaction as _implementation

sys.modules[__name__] = _implementation
