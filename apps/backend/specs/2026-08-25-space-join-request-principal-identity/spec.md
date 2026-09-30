# Space Join Request Uses the Authenticated Human Identity

## Summary

`POST /openapi/v1/bots/spaces/{space_id}/join-requests` is a non-delegable
self-service operation. It does not accept an acting `user_id` or `user_name`
from the request. The backend derives the applicant id from the verified human
Principal and resolves the display nickname through `StaffDeptPlugin`.

This operation does not support an App acting on behalf of a user.

## Contract

The request body remains:

```json
{
  "reason": "申请加入空间"
}
```

- `user_id` is not a declared query or body parameter.
- `user_name` is not a declared query or body parameter.
- Legacy or forged query values do not override the authenticated Principal.
- A Principal that names no human user is refused.
- The applicant id passed into Core is the verified Principal's user id.

## Nickname Resolution

After Space eligibility checks succeed, `WorkOrderService` calls:

```python
staff_dept.get_profile_by_work_no(work_no=applicant_user_id)
```

The notification applicant name is selected as follows:

1. trimmed employee nickname, truncated to 128 characters;
2. otherwise the authenticated applicant user id;
3. `StaffProfileLookupError` also falls back to the applicant user id.

The applicant user id remains the authoritative persisted identity. The current
repository uses `applicant_name` to render notification content; this change
does not add a new structured WorkOrder nickname column.

## Compatibility

This is an intentional exception to the 2026-08-08 explicit-user-id rule. That
rule continues to apply to delegable user-scoped operations. This endpoint is
instead classified as authenticated-human-self because user delegation is not a
supported capability.

Clients should remove `user_id` and `user_name` from calls to this operation.
Unknown legacy query parameters may be ignored during migration but have no
effect on identity.

## Acceptance Criteria

- A human caller succeeds without a `user_id` query parameter.
- A forged query `user_id` cannot change `applicant_user_id`.
- App-only and unauthenticated callers are refused.
- A returned nickname is trimmed and limited to 128 characters.
- Missing, blank, or failed nickname lookup falls back to the applicant user id.
- Invalid Space requests do not call the staff directory.
- The Backend-generated OpenAPI publishes no `user_id` parameter for this
  operation. Updating the pinned Gateway artifact is handled separately when its
  existing full-schema drift is reconciled.
