"""Authorization rows of ``/openapi/v1/bots/metadata/*``, merged into ``AUTHORIZATION``.

Tenant-wide display metadata routes grouped here so ``AUTHORIZATION`` stays
under the source size cap; row contents are unchanged.
"""

from .models_authorization import Authorization, NoCheck

METADATA_AUTHORIZATION: dict[tuple[str, str], Authorization] = {
    ("GET", "/openapi/v1/bots/metadata/digital-employees"): NoCheck(
        "authenticated tenant catalog for digital employee registration; creator filter is supplied by the platform"
    ),
    ("GET", "/openapi/v1/bots/metadata/digital-employees/{agent_id}"): NoCheck(
        "authenticated tenant catalog for digital employee registration; public visibility approved for this integration"
    ),
    ("POST", "/openapi/v1/bots/metadata/queries"): NoCheck(
        "tenant-wide display metadata for caller-supplied known bot ids"
    ),
}
