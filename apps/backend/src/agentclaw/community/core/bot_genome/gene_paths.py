"""Deprecated import compatibility; canonical implementation: ``avernet_genome.gene_paths``."""
import sys
from avernet_genome import gene_paths as _implementation

sys.modules[__name__] = _implementation
