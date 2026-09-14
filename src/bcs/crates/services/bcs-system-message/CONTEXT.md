# bcs-system-message Context

## Provides

System producers choose recipient text, Send/Inject intent and history visibility.
The dispatcher invokes the application SystemMessageQueueService before legacy
persistence or I/O. A successful queue batch owns all history for the event;
its managed recipients are never sent again through BotDeliveryPort. Failure
propagates without direct fallback. Off targets retain direct delivery after
batch commit, and human notices remain a separate frontend publication.
Recipient results distinguish durable admission from confirmed delivery.

## Consumes

Service API contracts for queue admission, Bot registry, delivery, run context,
message repository and frontend publication; domain/protocol DTOs.

## Boundaries

No dependency on concrete message-flow/store implementations. Bootstrap supplies
the queue port and binds the runtime owner. Producers do not query queue policy,
and this module does not schedule, select bounded history or own SQL transactions.

## Tests

`cargo test -p bcs-system-message` checks direct compatibility and producer rules.
`bcs-message-flow/tests/conformance_queued_system.rs` wires the real dispatcher,
producer, queue service and canonical store, then verifies Send preparation and
that managed initialization never calls native chat.inject.
