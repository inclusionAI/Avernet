# ClawEvolve Review v2 Protocol

Review consumes objective, evolution.spec.v1 contracts, post-Tune candidate optimization, candidate gates, aggregate validation, acceptance, Tune manifest, experiment ledger, and failure registry.

Review emits only `review_decision.json` with schema `evolution.review_decision.v2`.

Allowed content:

- failure signature
- hypothesis status and claim
- alternative causes
- disambiguation signal
- protected behaviors
- revisit condition

Forbidden content:

- mutation operator selection
- target file
- exact/proposed patch
- implementation instructions, Block/anchor design
- validation task IDs, per-case results, rubric/expert fields, transcripts, answers

The runner validates the decision, updates the runtime failure registry, constructs authoritative `spec-vN.json`, and deterministically renders `spec-vN.md` plus `spec_update_report.md`. Legacy review_decision.v1 is accepted only through a deterministic normalizer that discards implementation instructions.

Protected behaviors should be structured with behavior_id, description, observable metric, direction=maintain, min_value and max_drop. Legacy strings are resolved only through deterministic aliases; unknown strings remain unresolved and must be bound by Tune before candidate bench. Every protected behavior—resolved or unresolved—must be bound by `behavior_id` in the Tune manifest `protected_signals`; a binding must keep the same resolved metric with `min_value` not lower than and `max_drop` not wider than the Spec declares, and its `task_id` must be in the optimization task set. The protected gate must pass BOTH `candidate >= min_value` and `drop <= max_drop`; violating either rejects the candidate. `boolean_flip` expected-fix signals require `expected_value` (default `1`) and only the flip-toward-`expected_value` direction counts as a fix. Numeric signal fields must be finite numbers; non-numeric or non-finite values fail-closed the candidate gate instead of raising.

- Expected signals support `role=required|supporting` (default required). Required signals gate candidate effect; supporting signals are diagnostic only.
- Protected behaviors must be atomic: one behavior_id, one semantic behavior, one observable metric and one threshold/drop contract.
