# Delivery reaction protocol

This additive Channel Plugin API event lets BCS attach delivery status to the
exact IM message that created a queued delivery. BCS is the caller and the
channel provider is the callee.

## Envelope

BCS sends a `ChannelOutboundEvent` with these contract values:

- `kind`: `System`
- `purpose`: `Conversation`
- `text`: absent
- `render_hint`: `IgnoreByDefault`
- `source_im_message_id`: the provider message id to update
- `raw_payload`: the JSON object below

```json
{
  "type": "message.delivery.reaction",
  "state": "queued",
  "message_id": "durable-bcs-message-id"
}
```

`message_id` is the durable BCS source-message id for correlation. Providers
must attach, replace, or remove the reaction using `source_im_message_id`.

## States

| State | Provider behavior |
| --- | --- |
| `queued` | Show that the message is queued and expose `/abort` and `/cancel` guidance where the provider supports labels. |
| `processing` | Replace the queue status with the provider's existing processing or thinking reaction. |
| `expired` | Replace the queue status with an expired reaction. |
| `clear` | Remove the delivery-status reaction from this source message. Do not remove unrelated user reactions. |

Each state replaces the previous delivery-status reaction for the same
`source_im_message_id`. Providers that do not implement this additive event
ignore it because its render hint is `IgnoreByDefault`. Providers should also
ignore unsupported future states rather than rendering the raw payload.

For a source message with multiple Send targets, BCS keeps `queued` while any
target remains queued, then uses `processing` while a target is dispatching or
running. BCS emits `expired` only after no target remains queued or processing
and at least one target expired.

The Rust wire types are `DeliveryReactionEvent` and `DeliveryReactionState` in
`bcs-channel-api`. The protocol test in
`tests/delivery_reaction_protocol.rs` pins the discriminator, state spellings,
required fields, and round-trip decoding.
