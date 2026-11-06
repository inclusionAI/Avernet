# Task HTTP unknown-parameter compatibility

Approved by Shangjian on 2026-11-07, with BBS explicitly excluded.

## Contract
- Non-BBS task HTTP request DTOs ignore unrecognized keys, including typed nested
  report objects. Unknown keys are discarded, not forwarded into domain commands.
- Missing mandatory fields and invalid known values still fail validation.
- Authentication, authenticated owner resolution, relay generation fences, artifact
  ownership/content rules and task state checks are unchanged.
- Existing dictionary payloads and explicitly supported runtime extension fields
  retain their current behavior; this is not recursive removal of business data.
- BBS routes, schemas, queries and tests remain unchanged. This includes BBS claim
  and attach/result handlers, whose existing default behavior is preserved.
- Response schemas, core domain contracts, Skill local validation and CLI file
  validation are outside scope. No deployment, commit or push is requested.

## Acceptance
Valid requests with extra keys behave identically to requests without them;
missing/invalid known fields still fail. An ignored owner cannot override the
current authenticated Human; ignored fields cannot bypass relay generation or
become artifact/report/trace facts.
