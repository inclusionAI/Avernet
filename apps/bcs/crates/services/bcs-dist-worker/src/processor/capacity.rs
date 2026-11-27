//! Startup serialization proof for the fixed phase-one capacity profile.
//! These are independent field maxima (some cannot coexist in business state).
//! After the final externalization pass, payload bodies are at most 1 KiB.
//! Inline's tag is three bytes wider than Ref's tag, so inline bodies bound
//! both representations (the reference-only terminal field is checked too).
use super::{effects::CompletedEffects, lifecycle::finalize_independently};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, session::*};
use serde_json::{Value, json};
use std::{collections::BTreeMap, num::NonZeroU32};

pub(super) fn identity(n: usize) -> String {
    format!("{n:08}{}", "x".repeat(246))
}
pub(super) fn payload() -> Value {
    json!({"kind":"ref","value":{"key":"x".repeat(931),"digest":format!("sha256:{}","f".repeat(64))}})
}
fn framed_payload() -> Value {
    json!({"kind":"inline","value":"x".repeat(1022)})
}
fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId(identity(3)),
        principal: PrincipalId(identity(4)),
    }
}
fn content() -> Value {
    let mut interactions = Value::Array((0..64).map(|i| json!({
        "id": identity(i + 10), "origin": {"kind": "dispatch", "did": identity(i + 100)},
        "prompt": "", "kind": "authorization",
    })).collect());
    // Charge the full encoded list budget, including escaping and origins.
    let free = MAX_INTERACTION_METADATA_BYTES.saturating_sub(interactions.to_string().len());
    interactions[0]["prompt"] = Value::String("p".repeat(free));
    json!({"status":"input_required", "interactions":interactions, "snapshot":framed_payload(), "result_ref":null})
}
pub(super) fn snapshot() -> Result<TaskSnapshot, StoreError> {
    serde_json::from_value(json!({"schema_version":1,"scope":scope(),"sid":identity(1),"tid":identity(2),"content":content(),"output_limits":PHASE_ONE_OUTPUT_LIMITS,"output_usage":{"cumulative_bytes":8_388_608u64},"snapshot_event_id":{"commit":u64::MAX,"index":u32::MAX},"source_commit":u64::MAX,"created_at":i64::MIN,"updated_at":i64::MIN})).map_err(|_| invalid("capacity_shape"))
}
fn dispatch(i: usize) -> Result<EffectKind, StoreError> {
    serde_json::from_value(json!({"kind":"create_dispatch","spec":{"schema_version":1,"sid":identity(1),"tid":identity(2),"did":identity(i+100),"source":identity(5),"attempt":u32::MAX,"agent":identity(6),"pool":identity(9),"downstream_context":identity(8),"request":framed_payload(),"deadline":i64::MIN}})).map_err(|_| invalid("capacity_shape"))
}
pub(super) fn owner() -> SessionOwnership {
    SessionOwnership {
        sid: Sid(identity(1)),
        shard: ShardId(1),
        owner: ShardOwner {
            instance: Instance {
                node: identity(6),
                incarnation: identity(7),
            },
            epoch: WorkerEpoch(u64::MAX),
        },
    }
}
pub(super) fn candidate() -> Result<Checkpoint, StoreError> {
    let task = |i: usize| json!({"steps":u32::MAX,"tid":identity(i+2),"phase":{"kind":"queued"},"input":framed_payload(),"result_ref":null,"output_limits":PHASE_ONE_OUTPUT_LIMITS,"output_usage":{"cumulative_bytes":0},"publication":{"published_through":{"commit":u64::MAX,"index":u32::MAX},"pending_events":u32::MAX,"pending_bytes":u64::MAX,"flush_deadline":{"id":identity(i+900),"at":i64::MIN,"kind":{"kind":"snapshot","tid":identity(i+2)}}},"finalization":null});
    let deadline = |i: usize| json!({"id":identity(i+600),"at":i64::MIN,"kind":{"kind":"dispatch","tid":identity(2),"did":identity(i%64+100)}});
    let mut result:Checkpoint=serde_json::from_value(json!({
        "format_version":1,"downstream_generation":u64::MAX,"pending_cancel_did":identity(100),"commit_seq":u64::MAX,"consumed":{"sid":identity(1),"token":{"backend":identity(0),"generation":"1","value":format!("{}-{}",u64::MAX,u64::MAX)}},
        "tasks":(0..34).map(task).collect::<Vec<_>>(),
        "runner_state":{"tid":identity(2),"state":{"format_version":1,"value":"r".repeat(65_505)}},
        "dispatch_seq":(0..64).map(|i|(identity(i+100),json!({"tid":identity(2),"holder_epoch":u64::MAX,"sequence":u64::MAX,"terminal_seen":false}))).collect::<BTreeMap<_,_>>(),
        "deadlines":(0..34).map(deadline).collect::<Vec<_>>(),
        "deadline_history":(0..256).map(|i|(identity(i+600),deadline(i))).collect::<BTreeMap<_,_>>(),
        "view_drafts":{identity(2):content()},"last_event_id":{"commit":u64::MAX-1,"index":u32::MAX},"outbox":null,
    })).map_err(|_| invalid("capacity_shape"))?;
    result.deadlines.push(Deadline {
        id: DeadlineId(identity(999)), at: TimestampMillis(i64::MIN),
        kind: DeadlineKind::CancelGrace { did: Did(identity(100)) },
    });
    result.tasks[0].phase = TaskPhase::Running;
    let mut kinds = (0..31).map(dispatch).collect::<Result<Vec<_>, _>>()?;
    kinds.push(EffectKind::AppendEvents {
        previous: result.last_event_id,
        events: vec![OutputEvent {
            tid: Tid(identity(2)),
            id: EventId {
                commit: CommitSeq(u64::MAX),
                index: NonZeroU32::MIN,
            },
            body: OutputBody::Status {
                status: TaskStatus::InputRequired,
                interactions: snapshot()?.content.interactions,
            },
        }],
    });
    kinds.push(EffectKind::PublishSnapshot {
        snapshot: snapshot()?,
    });
    result.outbox = Some(CommittedBatch {
        created_at: Some(TimestampMillis(i64::MIN)),
        commit_id: CommitId(identity(999)),
        source_commit: result.commit_seq,
        content_digest: Digest("pending".into()),
        effects: vec![],
    });
    super::transitions::install_effects(&mut result, &owner().sid, kinds)?;
    Ok(result)
}

fn seal_candidate(
    owner: SessionOwnership,
    previous: CommitSeq,
    id: CommitId,
    mut candidate: Checkpoint,
    finalizing: bool,
    envelope_reserve: usize,
) -> Result<CommitRequest, StoreError> {
    candidate
        .outbox
        .as_mut()
        .ok_or_else(|| invalid("capacity_outbox"))?
        .created_at = Some(TimestampMillis(i64::MIN));
    super::commit::seal_candidate(owner, previous, id, candidate, finalizing, envelope_reserve)
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}

/// Validate this composition before any Session is admitted to the processor.
/// Smaller semantic limits are allowed, but every Task retains the fixed
/// physical frame. An undeclared or smaller provider cannot host that frame.
pub(super) fn validate(
    capacity: Option<SessionCheckpointCapacity>,
    envelope_reserve: usize,
) -> Result<(), StoreError> {
    let capacity = capacity.ok_or_else(|| invalid("checkpoint_capacity_missing"))?;
    if capacity.max_document_bytes < PHASE_ONE_OUTPUT_LIMITS.max_checkpoint_bytes as usize
        || capacity.max_envelope_bytes > envelope_reserve
        || capacity.max_envelope_bytes < 64 * 1024
        || !(64 * 1024..=128 * 1024).contains(&envelope_reserve)
    {
        return Err(invalid("checkpoint_capacity_mismatch"));
    }
    // One proof at the tightest supported reserve covers all admitted 64–128
    // KiB configurations. Configuration/provider matching is still checked on
    // every construction. No IO or process-specific state enters this cache.
    static PROOF: std::sync::OnceLock<Result<(), StoreError>> = std::sync::OnceLock::new();
    PROOF.get_or_init(|| validate_shapes(128 * 1024)).clone()
}

pub(super) fn validate_shapes(envelope_reserve: usize) -> Result<(), StoreError> {
    for ordinary in ordinary_candidates()? {
        let ordinary = seal_candidate(
            owner(),
            CommitSeq(u64::MAX - 1),
            CommitId(identity(999)),
            ordinary,
            false,
            envelope_reserve,
        )?;
        for task_index in [0, 1] {
            let mut before = ordinary.candidate.clone();
            before.commit_seq = CommitSeq(u64::MAX - 1);
            let mut metadata = snapshot()?;
            metadata.tid = before.tasks[task_index].tid.clone();
            // Also bound queued-task rejection while the Running task retains
            // its full private state and interaction draft. Independent field
            // extrema let this queued task own all cancellation obligations.
            for sequence in before.dispatch_seq.values_mut() {
                sequence.tid = metadata.tid.clone();
            }
            let done = completed(&before)?;
            let finalizing = finalize_independently(
                &before,
                &metadata,
                TerminalStatus::Completed,
                "TaskDeadlineOutOfRange",
                TimestampMillis(i64::MIN),
                Some(&done),
            )?;
            let finalizing = seal_candidate(
                owner(),
                before.commit_seq,
                CommitId(identity(999)),
                finalizing,
                true,
                envelope_reserve,
            )?;
            if super::encoding::encoded_size(
                &finalizing.candidate.outbox,
                PHASE_ONE_OUTPUT_LIMITS.outbox_finalization_reserve as usize,
            )
            .is_none()
            {
                return Err(invalid("finalization_reserve_insufficient"));
            }
            let mut before = finalizing.candidate;
            before.commit_seq = CommitSeq(u64::MAX - 1);
            // A previously committed FinishTask can freeze two 64 KiB inline
            // values. They may never be re-externalized during confirmation.
            // Keep that larger frozen state while exercising terminal publish.
            let frozen = before.tasks[task_index]
                .finalization
                .as_mut()
                .ok_or_else(|| invalid("capacity_finalization"))?;
            let inline = Payload::Inline(json!("x".repeat(64 * 1024 - 2)));
            frozen.record.snapshot = inline.clone();
            frozen.record.result = inline;
            let mut done = completed(&before)?;
            let reference = serde_json::from_value(payload()["value"].clone())
                .map_err(|_| invalid("capacity_reference"))?;
            done.saved.insert(metadata.tid.clone(), reference);
            let terminal = super::transitions::archive_confirmed(
                &before,
                &metadata,
                TimestampMillis(i64::MIN),
                &done,
            )?;
            seal_candidate(
                owner(),
                before.commit_seq,
                CommitId(identity(999)),
                terminal,
                true,
                envelope_reserve,
            )?;
        }
    }
    Ok(())
}

fn completed(checkpoint: &Checkpoint) -> Result<CompletedEffects, StoreError> {
    let batch = checkpoint
        .outbox
        .as_ref()
        .ok_or_else(|| invalid("capacity_outbox"))?;
    Ok(CompletedEffects {
        commit_id: batch.commit_id.clone(),
        sequence: batch.source_commit,
        digest: batch.content_digest.clone(),
        saved: Default::default(),
    })
}

/// At most one Status action is admitted. Its maximal interaction list and the
/// corresponding published snapshot dominate the extra effect in a 32-action
/// batch without Status. Check all other effect kinds instead of assuming that
/// CreateDispatch is always the largest after a public encoding change.
fn ordinary_candidates() -> Result<Vec<Checkpoint>, StoreError> {
    let baseline = candidate()?;
    let mut candidates = vec![baseline.clone()];
    let reference: Payload =
        serde_json::from_value(framed_payload()).map_err(|_| invalid("capacity_reference"))?;
    for variant in 0..3 {
        let mut candidate = baseline.clone();
        let mut kinds = Vec::new();
        for i in 0..31 {
            let kind = match variant {
                0 => EffectKind::RequestCancel {
                    did: Did(identity(i + 100)),
                    intent: CancelIntent {
                        request_id: RequestId(identity(i + 800)),
                        reason: Some(identity(i + 900)),
                    },
                },
                1 => EffectKind::PushInput {
                    did: Did(identity(i + 100)),
                    input: PendingInput {
                        seq: InputSeq(u64::MAX),
                        input_id: InputId(identity(i + 800)),
                        business_digest: Digest(identity(i + 900)),
                        payload: reference.clone(),
                    },
                },
                _ => EffectKind::AppendEvents {
                    previous: candidate.last_event_id,
                    events: vec![OutputEvent {
                        tid: Tid(identity(2)),
                        id: EventId {
                            commit: CommitSeq(u64::MAX),
                            index: NonZeroU32::MAX,
                        },
                        body: OutputBody::Artifact {
                            source: CorrelationKey(identity(5)),
                            artifact_id: ArtifactId(identity(i + 800)),
                            class: OutputClass::Output,
                            update: ArtifactUpdate::AppendText {
                                delta: reference.clone(),
                            },
                        },
                    }],
                },
            };
            kinds.push(kind);
        }
        kinds.extend(
            candidate
                .outbox
                .as_ref()
                .ok_or_else(|| invalid("capacity_outbox"))?
                .effects
                .iter()
                .skip(31)
                .map(|effect| effect.kind.clone()),
        );
        super::transitions::install_effects(&mut candidate, &owner().sid, kinds)?;
        candidates.push(candidate);
    }
    Ok(candidates)
}
