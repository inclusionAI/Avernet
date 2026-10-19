# Workflow version provenance

Version provenance is a server read model over existing repair publication,
approval, candidate revision, automatic-round, and deployment-history records.
No workflow YAML, tag protocol, table or migration is added.

An optimization source requires all of the following:

- A succeeded publication with a matching receipt (request, target, candidate,
  Pack digest, exact deployment number, version and deployment tag).
- Its approved human decision and published task revision match that candidate
  and the frozen publication baseline.
- The receipt resolves to the exact workflow deployment-history row.
- Automatic optimization additionally matches the frozen automatic origin to
  the persisted automatic draft intent and task association. A partial or
  missing automatic association is unknown, never reclassified as manual.

The actor is the trusted approving/publishing actor, independently of the Host's
legacy empty Bot fields. The recorded initial activation time comes from the
history row inserted in the activation transaction, never task completion or
Git tag creation time. UTC SQL timestamps and epoch seconds/milliseconds are
normalized without using the reader's local timezone.

An ordinary legacy deployment without this evidence has unknown source. Notes,
client labels and an earlier analysis are insufficient evidence. A rollback is
identified as a rollback and its restored deployment source is returned
separately. Deployment numbers remain independent even when spec versions match.

`WorkflowVersionSourceService.forWorkflow` loads one workflow's read snapshot;
`getSource` resolves an exact deployment. History routes apply their existing
workflow access check before reading this model. UI history and comparison use
the same source labels. Published repair tasks link directly to their receipt's
deployment and frozen baseline in the overview's version comparison view.

## Existing compatibility boundary

The legacy `setActive` endpoint can reactivate a prior deployment without writing
a new activation event or timestamp. The read model retains the initial
publication's source and does not manufacture a new activation event or claim
that its old timestamp is a reactivation time. Missing/ambiguous deployment
records and unsuccessful or unknown publication outcomes remain unknown.
