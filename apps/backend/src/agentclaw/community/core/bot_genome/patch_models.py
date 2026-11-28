"""Deprecated import compatibility; canonical implementation: ``avernet_genome.patch_models``."""
import sys
from avernet_genome import patch_models as _implementation

sys.modules[__name__] = _implementation
