#![allow(
    clippy::unwrap_used,
    reason = "serialize actual maximum-width public types"
)]
use super::{commit::seal_candidate, effects::CompletedEffects, lifecycle::finalize_independently};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, session::*};
use serde_json::{Value, json};
use std::{collections::BTreeMap, num::NonZeroU32};
fn identity(n: usize) -> String {
    format!("{n:08}{}", "x".repeat(246))
}
fn payload() -> Value {
    json!({"kind":"ref","value":{"key":"x".repeat(931),"digest":format!("sha256:{}","f".repeat(64))}})
}
fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId(identity(3)),
        principal: PrincipalId(identity(4)),
    }
}
fn content() -> Value {
    json!({"status":"auth_required","interactions":(0..64).map(|i|json!({"id":identity(i+10),"did":identity(i+100),"kind":"authorization"})).collect::<Vec<_>>(),"snapshot":payload(),"result_ref":null})
}
fn snapshot() -> TaskSnapshot {
    serde_json::from_value(json!({"schema_version":1,"scope":scope(),"sid":identity(1),"tid":identity(2),"content":content(),"output_limits":PHASE_ONE_OUTPUT_LIMITS,"output_usage":{"cumulative_bytes":8_388_608u64},"snapshot_event_id":{"commit":u64::MAX,"index":u32::MAX},"source_commit":u64::MAX,"created_at":i64::MAX,"updated_at":i64::MAX})).unwrap()
}
fn dispatch(i: usize) -> EffectKind {
    serde_json::from_value(json!({"kind":"create_dispatch","spec":{"schema_version":1,"sid":identity(1),"tid":identity(2),"did":identity(i+100),"node":identity(5),"attempt":u32::MAX,"target":{"agent":identity(6),"binding_version":identity(7),"credential_ref":identity(8)},"pool":identity(9),"capabilities":{"idempotent_start":true,"idempotent_input":true,"query_by_request":true,"query_by_task":true,"subscribe":true,"resume_events":true,"cancel":true},"request":payload(),"deadline":i64::MAX}})).unwrap()
}
fn owner() -> SessionOwnership {
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
fn candidate() -> Checkpoint {
    let task = |i: usize| json!({"steps":u32::MAX,"tid":identity(i+2),"phase":{"kind":"queued"},"input":payload(),"result_ref":null,"output_limits":PHASE_ONE_OUTPUT_LIMITS,"output_usage":{"cumulative_bytes":0},"publication":{"published_through":null,"pending_events":0,"pending_bytes":0,"flush_deadline":null},"finalization":null});
    let deadline = |i: usize| json!({"id":identity(i+600),"at":i64::MAX,"kind":{"kind":"dispatch","tid":identity(2),"did":identity(i%64+100)}});
    let mut result:Checkpoint=serde_json::from_value(json!({
        "format_version":1,"commit_seq":u64::MAX,"consumed":{"sid":identity(1),"token":{"backend":identity(0),"generation":"1","value":format!("{}-{}",u64::MAX,u64::MAX)}},
        "tasks":(0..34).map(task).collect::<Vec<_>>(),
        "runner_state":{"tid":identity(2),"state":{"format_version":1,"value":"r".repeat(65_505)}},
        "dispatch_seq":(0..64).map(|i|(identity(i+100),json!({"tid":identity(2),"holder_epoch":u64::MAX,"sequence":u64::MAX}))).collect::<BTreeMap<_,_>>(),
        "deadlines":(0..34).map(deadline).collect::<Vec<_>>(),
        "deadline_history":(0..256).map(|i|(identity(i+600),deadline(i))).collect::<BTreeMap<_,_>>(),
        "view_drafts":{identity(2):content()},"last_event_id":{"commit":u64::MAX-1,"index":u32::MAX},"outbox":null,
    })).unwrap();
    result.tasks[0].phase = TaskPhase::Running;
    let mut kinds = (0..31).map(dispatch).collect::<Vec<_>>();
    kinds.push(EffectKind::AppendEvents {
        previous: result.last_event_id,
        events: vec![OutputEvent {
            tid: Tid(identity(2)),
            id: EventId {
                commit: CommitSeq(u64::MAX),
                index: NonZeroU32::MIN,
            },
            body: OutputBody::Status {
                status: TaskStatus::AuthRequired,
                interactions: snapshot().content.interactions,
            },
        }],
    });
    kinds.push(EffectKind::PublishSnapshot {
        snapshot: snapshot(),
    });
    result.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: CommitId(identity(999)),
        source_commit: result.commit_seq,
        content_digest: Digest("pending".into()),
        effects: vec![],
    });
    super::transitions::install_effects(&mut result, &owner().sid, kinds).unwrap();
    result
}
#[test]
fn independent_field_maxima_fit_ordinary_and_reserved_finalization_frames() {
    // This intentionally combines independent extrema, including 34 tasks and
    // mutually impossible full dispatch sets, to upper-bound legal candidates.
    assert_eq!(serde_json::to_vec(&identity(1)).unwrap().len(), 256);
    assert_eq!(serde_json::to_vec(&payload()["value"]).unwrap().len(), 1024);
    let ordinary = seal_candidate(
        owner(),
        CommitSeq(u64::MAX - 1),
        CommitId(identity(999)),
        candidate(),
        false,
        64 * 1024,
    )
    .unwrap();
    assert!(
        serde_json::to_vec(&ordinary.candidate.outbox)
            .unwrap()
            .len()
            <= 192 * 1024
    );
    assert!(serde_json::to_vec(&ordinary.candidate).unwrap().len() <= 896 * 1024);
    let mut before = ordinary.candidate;
    before.commit_seq = CommitSeq(u64::MAX - 1);
    let batch = before.outbox.as_ref().unwrap();
    let done = CompletedEffects {
        commit_id: batch.commit_id.clone(),
        sequence: batch.source_commit,
        digest: batch.content_digest.clone(),
        saved: Default::default(),
    };
    let finalizing = finalize_independently(
        &before,
        &snapshot(),
        TerminalStatus::Failed,
        "OutputQuotaExceeded",
        TimestampMillis(i64::MAX),
        Some(&done),
    )
    .unwrap();
    let finalizing = seal_candidate(
        owner(),
        before.commit_seq,
        CommitId(identity(999)),
        finalizing,
        true,
        64 * 1024,
    )
    .unwrap();
    assert!(
        serde_json::to_vec(&finalizing.candidate.outbox)
            .unwrap()
            .len()
            <= 64 * 1024
    );
    assert!(serde_json::to_vec(&finalizing.candidate).unwrap().len() <= 960 * 1024);
}
