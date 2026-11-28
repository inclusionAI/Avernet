# Internal context collaboration HTTP adapter

This package provides the existing context collaboration routes and two product
creation routes. The HTTP composition root mounts its exported `router` through
[collaboration registration](../collaboration_registration.py). The former
`task.context_collab` module import and its public handler exports remain valid.

## Product creation

Both routes require the normal internal HTTP authentication. The shared
[product handlers](../product_profiles.py) inject `ProductProfileServiceProtocol`
and bind `ActorContext(PrincipalRef("human", user.staffId), "api")` for the call.
Actor, tenant/environment and authorization facts cannot be supplied in JSON.

| Method and path | JSON body | Service call |
| --- | --- | --- |
| `POST /api/v1/collaboration/task/context_collab/product_profiles` | `name`, `stages` | `create_product_profile(name, stages)` |
| `POST /api/v1/collaboration/task/context_collab/collets` | `profile_id`, `content` | `create_product_collet(profile_id, content)` |

Profile example:

```json
{
  "name": "coding",
  "stages": "stages:\n  - name: build\n    candidates: [{type: bot, uuid: worker}]\n    depends_on: []\n    acceptance: {condition: tests pass}\n"
}
```

The service installs MultiRollout NEST Swarm with a static YAML workflow;
`composition` is not a request field. The response still contains the complete
generated composition. Use the returned Profile `id` to create a Collet:

```json
{
  "profile_id": 1,
  "content": "Implement the feature and run its tests"
}
```

Successful responses use the existing HTTP 200 Envelope. Profile data contains
`id`, `name`, `composition`, `stages`; Collet data contains `id`, `profile_id`,
`content`, `status`. `profile_id` is a positive integer, not a boolean or numeric
string. Missing/invalid fields and unknown fields fail with 422. `name` is
bounded to 256 characters; `stages` and `content` to 262144 characters at the HTTP
schema boundary, with domain UTF-8 byte limits still enforced by the service.
The pre-parse body limit is 2 MiB, including streamed requests (413 on overflow).

Creation is an independent invocation, not an idempotent retry API. Publication,
Profile use and execution capability checks remain service responsibilities.
Authentication failure prevents service invocation; domain authorization,
not-found, conflict and validation failures retain the shared error mapping.
Inline configuration and content remain redacted in error logs. Both creation
handlers also emit an INFO entry diagnostic (`product_profile.http.create.start`
or `product_collet.http.create.start`) containing only the actual route, the
response `request_id`, the caller `X-Request-ID` and bounded metadata/byte sizes.

## Compatibility and runtime boundary

The original `POST /api/v1/collaboration/task/products/profiles` and
`POST /api/v1/collaboration/task/products/collets` paths remain supported. Both
path pairs use the same handlers, schemas, authentication scope and body limits.
The original context create/event/status routes are unchanged. The canonical
product adapter also exposes `PATCH /products/profiles/{profile_id}` for
Profile updates; it is not duplicated under this alias in this phase. There are
no new public `/openapi/v1` routes, but the Service API now includes the
restricted `update_product_profile` method and uses the same DI bindings.

These routes call the currently bound ProductProfileService. They do not switch
its persistence to the relational-storage foundation or start a worker. The
[product runtime contract](../../../../core/task/context_collab/l6_products/product_profile_service/README.md)
owns required host capabilities, authorization, storage and scheduling. Public
unconfigured capabilities still fail closed; example Bot IDs are not grants.

## Change impact and verification

Consumers can use the new context-collab paths without changing the creation
payload. Future changes to shared handlers affect both path pairs and must keep
their authentication, request guards, serialization and error behavior aligned.
Tests cover injected service calls and actor provenance, strict request schemas,
auth failures, domain failures, size guards and original route registration;
registered endpoint cases exercise the real ProductProfileService and SQLite.
