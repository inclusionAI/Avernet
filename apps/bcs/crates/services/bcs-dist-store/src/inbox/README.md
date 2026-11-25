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

An unconfirmed request can still execute. Retrying the same scoped InputId
resumes its original admission; creating the document alone does not guarantee
background completion. No later accepted input may depend on that retry:

- When a reader finds an undecided Inbox row, it checks the stored request and
  can select the observed append position by EQ if the producer lost its reply.
  It helps finish activation and rereads the decision before advancing coverage.
  Only the accepted position is delivered; physical duplicates are skipped.
- If registration would exceed the Session slot or byte limit, the producer
  helps one existing eligible slot within its current time budget and rechecks
  capacity. Further attempts rotate through eligible slots. A confirmed conflict
  with an existing SessionSpec or Task identity records `ContentConflict` in the
  slot before copying the decision to the admission document and releasing the
  slot. It does not append or activate the input. Only the closure provider may
  release a closed-generation Start; capacity pressure is never proof of completion.

Neither path scans other Sessions or requires a shard journal.

Connector key reports use the same admission record and activation path. The Store
gets scope from the existing SessionSpec and checks the stable event identity
(dispatch, event kind and question round). The first accepted payload wins: a
new holder can observe different bytes for the same remote outcome. A different
terminal state for that identity conflicts. Gateway requests retain their normal
content digest.

## Scheduling and consumption

The schedule document owns the sealed Session owner, activation version,
next-check, listening flag and admission slots. `active` and `wake` are
notifications used to discover work; they are not authority. Worker takeover
enumerates the durable active index and then validates schedule/checkpoint state.
Each page uses one bounded range read and reserves up to the requested rows
from the Discovery log budget. Visits to Session state have their own quota.
If the log quota or shared IO slots run out, no page is read and the Worker
retries from the same cursor. The continuation is based on the granted page
size, including if the requested upper bound was larger.
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
