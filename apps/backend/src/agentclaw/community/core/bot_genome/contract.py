"""Deprecated import compatibility; canonical implementation: ``avernet_genome.contract``."""
import sys
from avernet_genome import contract as _implementation

sys.modules[__name__] = _implementation
