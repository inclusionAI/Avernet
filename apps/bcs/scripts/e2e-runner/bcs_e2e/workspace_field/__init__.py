"""Private Workspace field-density contract family.

The dual-environment case runner admits one bounded profile per tier,
executes the 43-obligation offline projection locally over the 63-atom
oracle, and fail-closes the preprod leg with named inventoried gaps.  Only a
profile digest reaches the state directory; credential values never do.
"""

from .dual_case_run import (
    CASE_ID,
    DUAL_WORKSPACE_FIELD_COMMAND,
    GAP_CONTRACT,
    LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED,
    PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED,
    REPORT_CONTRACT,
    dual_workspace_field_case_run,
    dual_workspace_field_command_handler,
)
from .profile import (
    ALLOWED_ENV_REFERENCES,
    DRIVER_TOKEN_ENV,
    HUMAN_COOKIE_ENV,
    PROFILE_CONTRACT,
    admit_workspace_field_profile,
    credential_refs,
    default_workspace_field_profile,
    load_workspace_field_profile,
)

__all__ = [
    "ALLOWED_ENV_REFERENCES",
    "CASE_ID",
    "DRIVER_TOKEN_ENV",
    "DUAL_WORKSPACE_FIELD_COMMAND",
    "GAP_CONTRACT",
    "HUMAN_COOKIE_ENV",
    "LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED",
    "PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED",
    "PROFILE_CONTRACT",
    "REPORT_CONTRACT",
    "admit_workspace_field_profile",
    "credential_refs",
    "default_workspace_field_profile",
    "dual_workspace_field_case_run",
    "dual_workspace_field_command_handler",
    "load_workspace_field_profile",
]
