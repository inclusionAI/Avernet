# Session deletion investigation

Start from the exact request, Session ID, Group ID if supplied, receiver, response and target revision. Do not equate a Group with one Session.

| Observation | Distinguishing evidence | Next place to inspect |
| --- | --- | --- |
| Rejected request | Is the canonical session ID present and does any provided group match? | [Protocol request and validation](../session-delete-protocol.md). |
| `DELETE_FAILED` | The same receiver's gateway delete result and its target Session key | Protocol failure semantics and the receiver's conformance tests. |
| Other sessions remain | Exact targeted Session and receiver result | Sibling retention is expected; the contract does not authorize group-wide deletion. |
| Public delete succeeded but Bot history remains | Whether an actual RPC sender delivered this separate request | [Compatibility and callers](../session-delete-protocol.md); the document does not establish an automatic sender. |

The protocol and tests explain allowed behavior; runtime delivery and historical state require independent evidence.
