"""Deprecated import compatibility; canonical implementation: ``avernet_evolution.core.evolution_ledger.ledger``."""
import sys
from avernet_evolution.core.evolution_ledger import ledger as _implementation

sys.modules[__name__] = _implementation
