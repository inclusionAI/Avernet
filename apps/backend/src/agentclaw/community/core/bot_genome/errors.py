"""Deprecated import compatibility; canonical implementation: ``avernet_genome.errors``."""
import sys
from avernet_genome import errors as _implementation

sys.modules[__name__] = _implementation
