"""Deprecated import compatibility; canonical implementation: ``avernet_genome.limits``."""
import sys
from avernet_genome import limits as _implementation

sys.modules[__name__] = _implementation
