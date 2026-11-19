"""Deprecated import compatibility; canonical implementation: ``avernet_genome.models``."""
import sys
from avernet_genome import models as _implementation

sys.modules[__name__] = _implementation
