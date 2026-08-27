"""``GET /api/v1/org/user`` — directory-identity read by ``?user_id=``.

A sibling of ``GET /openapi/v1/org/user`` at a separate prefix, with its own
access seam (``dependencies.require_org_user_caller`` over the cached,
signature-verified ``resolve_caller``). Any verified principal may look a user
up; an app-only caller is admitted (no ``refuse_app_only_caller`` here).
"""
