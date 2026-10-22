# Bot inventory entity identity

## Contract

`GET /openapi/v1/bots/all` returns `entity_id` and `owner_entity_id` as separate
identity fields on every inventory item. `entity_id` is nullable for legacy or
incomplete source rows; consumers must fail closed when a team Bot has no value.

- `entity_id` is the entity that addresses the Bot record. For a team Bot this
  is the team entity identifier used in a composite chat identity
  (`bot_id:entity_id`).
- `owner_entity_id` remains the user identifier that owns the Bot and remains
  the owner context for Bot runtime APIs.
- Personal records may have the same value in both fields.

The field is additive and nullable. Existing action, lifecycle, owner, and space
semantics do not change. Consumers must not derive a team `entity_id` from
`owner_entity_id`, or derive `owner_entity_id` from `entity_id`.

## Reason

The BotWorkshop inventory previously returned `owner_entity_id` without the
Bot record's `entity_id`. Frontend mapping used that owner field as the missing
entity, so the row-level conversation action built a team route addressed to
the owner instead of the team Bot entity.

## Validation

- Backend inventory service preserves a source row's distinct team `entity_id`
  and owner identifier.
- The `/bots/all` HTTP response publishes both fields independently.
- Frontend mapping and BotWorkshop navigation route with `entity_id` while
  preserving `owner_entity_id` as the runtime owner.
