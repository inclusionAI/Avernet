# Reliable Inbox provider

`StoreInboxProducer` implements admission, replay, scheduling and worker
maintenance over `DocumentCas`, `Stream` and `ZSet`. The admission authority is
one document for each `(scope, input_id)`; `schedule:<sid>`, `ckpt:<sid>` and
`inbox:<sid>` remain separate Session-local documents/streams.

## Admission state machine

1. Validate the authenticated scope, session/task identity, request digest and
   physical encoded-size limits.
2. Create the admission document with document CAS. The document stores the
   request, generation, phase, schedule registration, append credit, Inbox
   position and final decision. There is no shard-wide admission root, sequence
   journal, intent document or canonical document.
3. Register the admission key in the Session schedule, then create the frozen
   session spec and task/index records with create-once semantics.
4. Append the logical input to the Session Inbox. A CAS transition records the
   selected physical position; a duplicate physical append remains harmless and
   is classified from the admission document.
5. CAS the decision and schedule activation. A retry with the same scoped
   InputId and digest resumes any incomplete phase and returns `Duplicate` after
   a previously accepted record is observed. A different digest returns
   `ContentConflict`.

The recovery guarantee starts when the admission document exists. If the
create-if-absent write never succeeds, the provider does not promise background
recovery; the caller retries the same scoped InputId. A lost response after the
create is repaired by the same retry. Admission writes use bounded JSON CAS;
no Lua script or cross-key transaction is required.

## Scheduling and consumption

The schedule document owns the sealed Session owner, activation version,
next-check, listening flag and admission slots. `active` and `wake` are
notifications used to discover work; they are not authority. Worker takeover
enumerates the durable active index and then validates schedule/checkpoint state.
A new admission invalidates an old schedule observation, and `settle` checks the
owner and token atomically.

The feed keeps each Session watch installed across IO. It reports failed watches
separately, preserves a confirmed contiguous read position, and classifies
physical duplicates through the admission document. `DeliveryReceipt` remains
an ephemeral acknowledgement handle; only a committed checkpoint proof can
advance a read position.

## Closure and maintenance

Admission maintenance hooks remain for Worker health compatibility, but they do
not scan a global journal. Closure reads Session-local schedule/checkpoint state
and verifies terminal task evidence before releasing a closed-generation slot.
The compatibility `journal_cut` field is always zero in this MVP and carries no
shard-wide ordering meaning.

## Retained limits

The provider still enforces per-request encoded input size, per-document size,
per-schedule slot and byte limits, reader/watch/receipt limits, and shared scan
IO/rate budgets. It does not maintain namespace-lifetime admission history,
retained-history byte, reserved-history or distinct-Session ledgers. Output usage
limits belong to task finalization and remain independent of admission.
