"""Deprecated import compatibility; canonical implementation: ``avernet_genome.spec_models``."""
import sys
from avernet_genome import spec_models as _implementation

sys.modules[__name__] = _implementation
