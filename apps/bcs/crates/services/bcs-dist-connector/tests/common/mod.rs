//! Fixtures shared by the engine's tests.
#![allow(dead_code, clippy::unwrap_used, clippy::expect_used, reason = "test support")]

use bcs_dist_model::*;

pub const DID: &str = "s0042.7f3a9c.b21e.n2.a1";

pub fn caps() -> DownstreamCapabilities {
    DownstreamCapabilities {
        idempotent_start: false,
        idempotent_input: false,
        query_by_request: false,
        query_by_task: true,
        subscribe: true,
        resume_events: false,
        cancel: true,
        isolated_memory: false,
        persistent_dedupe: false,
    }
}

pub fn spec() -> DispatchSpec {
    DispatchSpec {
        schema_version: 1,
        sid: Sid("s0042.7f3a9c".into()),
        tid: Tid("s0042.7f3a9c.b21e".into()),
        did: Did(DID.into()),
        node: NodeId("n2".into()),
        attempt: 1,
        target: AgentBinding {
            agent: AgentId("agent-analyst".into()),
            binding_version: BindingVersion("3".into()),
            credential_ref: CredentialRef("cred://agent-analyst/v3".into()),
        },
        pool: PoolId("a2a".into()),
        capabilities: caps(),
        request: Payload::Inline(serde_json::json!({"parts": [{"text": "compare"}]})),
        deadline: TimestampMillis(1_794_371_505_000),
    }
}

pub fn instance(node: &str) -> Instance {
    Instance {
        node: node.into(),
        incarnation: "1794370950000".into(),
    }
}

/// A dispatch as the Worker creates it.
pub fn created() -> DispatchView {
    DispatchView {
        spec: spec(),
        control: DispatchControlState {
            cancel: None,
            pending_input: Vec::new(),
        },
        execution: DispatchExecution {
            holder: None,
            phase: DispatchPhase::Created,
            remote: None,
            confirmed_input_through: None,
            cancellation: CancelObservation::NotSent,
            outcome: None,
            send_proof: None,
            verification: None,
            quarantined: None,
        },
    }
}

pub fn remote() -> RemoteTaskRef {
    RemoteTaskRef {
        task_id: "rt-77c2".into(),
        context_id: Some("ctx-91".into()),
    }
}

pub fn in_phase(phase: DispatchPhase) -> DispatchView {
    let mut view = created();
    view.execution.phase = phase;
    if phase == DispatchPhase::Accepted {
        view.execution.remote = Some(remote());
    }
    view
}

pub fn pending(seq: u64) -> PendingInput {
    PendingInput {
        seq: InputSeq(seq),
        input_id: InputId(format!("in-{seq}")),
        business_digest: Digest(format!("sha256:{seq}")),
        payload: Payload::Inline(serde_json::json!({"parts": [{"text": format!("reply {seq}")}]})),
    }
}

pub fn cancel_intent() -> CancelIntent {
    CancelIntent {
        request_id: RequestId("cancel-1".into()),
        reason: None,
    }
}

pub fn state(word: &str) -> Payload {
    Payload::Inline(serde_json::json!({ "state": word }))
}
