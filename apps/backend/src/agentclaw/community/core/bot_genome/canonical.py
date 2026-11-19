"""Deprecated import compatibility; canonical implementation: ``avernet_genome.canonical``."""
import sys
from avernet_genome import canonical as _implementation

sys.modules[__name__] = _implementation
