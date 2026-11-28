"""Compatibility import; canonical pure codec is shared with Evolution."""
import sys
from avernet_genome import text_edits as _implementation
sys.modules[__name__] = _implementation
