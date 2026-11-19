"""Deprecated import compatibility; canonical implementation: ``avernet_genome.tree``."""
import sys
from avernet_genome import tree as _implementation

sys.modules[__name__] = _implementation
