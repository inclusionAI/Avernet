# Gateway: inbound A2A server of the cluster executor plane

Status: proposal, plan reviewed on the PR (2026-11-17). No runtime code yet.

## Summary

The Gateway is the tier that personal assistants (PAs) talk to. It accepts A2A
requests, records each input durably so a Worker will run it, and serves the
task's state and output back to the caller, including over a resumable stream.
It never talks to a Worker or a Connector directly: everything goes through the
store.

## Motivation

The contracts for the three roles (Gateway, Worker, Connector) and the shared
store pieces are merged, but nothing serves a PA yet. The Gateway is the first
visible piece: without it no input can enter the plane and no output can leave
it. The Worker belongs to another team, so the Gateway is built against the
merged contracts and tested with stand-ins for the Worker's side.

## Terms

| Term | Meaning |
| --- | --- |
| PA | the client; a personal assistant calling us over A2A |
| session | one conversation; the A2A `contextId`. Its id names the shard it lives in |
| task | one unit of work inside a session; the A2A `taskId`. Tasks of a session run one at a time |
| input | something admitted for a task: a user message, a reply to a question, or a cancel |
| admission | recording an input durably and making sure a Worker will notice it |
| snapshot | the stored current view of a task: status, open questions, display state |
| event | one committed piece of task output (status change, output, final result) |
| resume id | the opaque id sent with every streamed event; a client sends the last one back to continue |

## User stories

- As a PA, I send a message and get a task back, so I can start work in a new
  or an existing session.
- As a PA, I send a message and receive the task's output as a stream, so I can
  show progress while it runs.
- As a PA whose connection dropped, I reconnect with my last resume id and get
  exactly the events I missed, on any Gateway instance.
- As a PA joining late, I subscribe to a task and get its current state first,
  then everything after it.
- As a PA, I answer a question the task asked, and the task continues.
- As a PA, I cancel a task and get its state back within a few seconds.
- As a PA, I fetch one task, or list the tasks of a session.
- As an operator, I can lose a Gateway instance without losing an accepted
  input or a client's ability to resume.

## Acceptance criteria

Admission
- [ ] A message without a session starts a new session and a new task; with a
      session and no task it starts a new task there; with a task it is a reply
      to that task's open question.
- [ ] Sending the same message again (same `messageId`, same content) returns
      the same session and task, and the input takes effect once.
- [ ] The same `messageId` with different content is rejected as a conflict.
- [ ] "Accepted" is only reported once the input, the task and the Worker's
      notification are all recoverable. Any failure in between is reported as
      "outcome unknown, retry the same request", and the retry completes it.
- [ ] A reply is rejected when the task is finished, the question is not open,
      or the task belongs to another session or caller.
- [ ] A caller can only read or change sessions and tasks of its own tenant and
      principal.
- [ ] A session or task is only reachable through the group it was created
      for; through another group it is reported as not found.

Query
- [ ] Fetching a task returns its stored snapshot as is. Status, open questions
      and final results are current; in-progress text may lag by one refresh.
- [ ] A finished task whose live record has expired is still returned, from the
      final record.
- [ ] Listing returns the tasks of one session, live and finished, in creation
      order, in pages, optionally filtered by status. A session filter is
      required.

Streaming
- [ ] A new subscriber gets the snapshot first, then the events after it, then
      live events, with no gap and no duplicate.
- [ ] A reconnect with a valid resume id gets only the events after it, with no
      snapshot. An unusable resume id falls back to snapshot-first.
- [ ] A stream ends after the task asks for input or finishes. Disconnecting
      does not affect the task.
- [ ] Subscribing to a task that has already finished is refused with the A2A
      "unsupported operation" error; the caller fetches the task instead.
- [ ] Subscribers of the same session on one Gateway share one store read; a
      slow subscriber is closed with a retryable error and never delays others.
- [ ] A resume id is opaque, works on any Gateway instance, and carries a
      format version.

Blocking send and cancel
- [ ] A blocking send returns when the task asks for input or finishes, with a
      snapshot that includes that event; after 60 s it returns the current
      state.
- [ ] Cancel records the request, waits up to about 4 s for a final state and
      returns the task (the current state on timeout). Cancelling a finished
      task is refused with the A2A "task not cancelable" error.

A2A surface
- [ ] A2A 1.0 over JSON-RPC with SSE: SendMessage, SendStreamingMessage,
      GetTask, SubscribeToTask, CancelTask, ListTasks.
- [ ] Push notifications are declared unsupported in the agent card; any push
      configuration call, or a webhook in a send, gets the A2A "push
      notification not supported" error.
- [ ] Every error in the design's error table maps to the listed A2A code.

Delayed contracts
- [ ] The admission step that learns whether a Worker is listening stays in its
      place in the order, fails explicitly with "need contract, not implemented
      yet", and admission then always notifies the shard. A test asserts this.

## In scope

- The Gateway's use cases, its rules, and the store-backed readers it needs.
- The A2A JSON-RPC + SSE delivery adapter.
- The approved edits to the merged contracts (see plan).
- A per-session task index so tasks can be listed.

## Out of scope

- The Worker and the Connector, and anything they write.
- gRPC, WebSocket, push notifications, A2A 0.3 method names.
- Folding later events onto the snapshot for fetch and non-streaming send.
- Layout and retention of final records (read through the existing contract).
- Authentication placement; the adapter takes an already-resolved caller.
- Process wiring into a deployable binary and the production store client.
- Session-level subscriptions, outbound safety policy, load testing.

## Open questions

See the "Questions for review" section of [plan.md](plan.md).
