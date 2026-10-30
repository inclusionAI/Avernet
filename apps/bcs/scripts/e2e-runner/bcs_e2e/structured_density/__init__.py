"""Dual-environment structured-collaboration density slice.

The package projects the 43 reviewed assertion sites of the structured
collaboration story (17 request-level parent obligations plus 26 field-level
density rows) and executes the reviewed 17-request live journey on admitted
profiles: owned-local Singlebox keeps one origin for both families, while
preprod splits the journey across its two real wire origins (station-direct
legacy face for the structured obligations, Gateway public front door with an
explicit browser origin for the catalog observations).  Offline projection
without an injected transport stays the honest BLOCKED/NOT_RUN receipt.  No
credential value ever leaves the package.
"""

from .contract import (
    DENSITY_ASSERTION_COUNT,
    DENSITY_PARENT_COUNT,
    NEW_ASSERTION_COUNT,
    parsed_assertions,
    structured_density_plan,
)
from .live_transport import JOURNEY_STEPS, LiveStructuredDensityTransport
from .profile import (
    DENSITY_STRUCTURED_OPERATIONS,
    DENSITY_TEMPLATES_OPERATIONS,
    validate_density_profile,
    validate_density_templates_profile,
)
from .run import run_structured_density

__all__ = [
    "DENSITY_ASSERTION_COUNT",
    "DENSITY_PARENT_COUNT",
    "DENSITY_STRUCTURED_OPERATIONS",
    "DENSITY_TEMPLATES_OPERATIONS",
    "JOURNEY_STEPS",
    "NEW_ASSERTION_COUNT",
    "LiveStructuredDensityTransport",
    "parsed_assertions",
    "run_structured_density",
    "structured_density_plan",
    "validate_density_profile",
    "validate_density_templates_profile",
]
