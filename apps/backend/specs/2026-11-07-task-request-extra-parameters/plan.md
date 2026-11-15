# Implementation plan

Use the existing Pydantic DTOs, changing request-only extra=forbid to extra=ignore:
search, dispatch, intake/start, grant/revoke/settings, standalone artifacts and
nested relay report/event/planned-node/trace/artifact request objects.
Keep TaskDispatchResultDTO strict and preserve existing extra=allow extension models.
No shared global BaseModel changes, route changes or alternate protocol.

Update the reviewed Relay/task-cli documentation. Tests first demonstrate the
old rejection, then assert discarded extras, mandatory fields, invalid known
values, nested projection, extension preservation, authenticated owner resolution
and generation checks. Run both task HTTP surfaces, artifact core tests, relay
integration and task service tests. Run unchanged BBS suites for regression only.
