use super::*;
use serde_json::json;

pub fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("tenant-a".into()),
        principal: PrincipalId("caller-a".into()),
    }
}

pub fn instance() -> Instance {
    Instance {
        node: "worker-a".into(),
        incarnation: "boot-2".into(),
    }
}

pub fn owner() -> SessionOwnership {
    SessionOwnership {
        sid: Sid("session-a".into()),
        shard: ShardId(42),
        owner: ShardOwner {
            instance: instance(),
            epoch: WorkerEpoch(14),
        },
    }
}

pub fn position() -> InboxPos {
    InboxPos {
        sid: owner().sid,
        token: ReplayToken {
            backend: BackendId("fixture".into()),
            generation: LogGeneration("generation-2".into()),
            value: "position-8".into(),
        },
    }
}

pub fn proof() -> CommitReceipt {
    CommitReceipt {
        sid: owner().sid,
        owner: owner().owner,
        commit_id: CommitId("commit-8".into()),
        content_digest: Digest("checkpoint-digest".into()),
        sequence: CommitSeq(8),
        consumed: Some(position()),
        proof: "commit-proof".into(),
    }
}

pub fn source() -> EffectSource {
    EffectSource {
        commit: proof(),
        effect_id: EffectId {
            sid: owner().sid,
            commit: CommitSeq(8),
            index: NonZeroU32::MIN,
        },
        content_digest: Digest("effect-digest".into()),
    }
}

pub fn payload() -> Payload {
    Payload::Inline(json!({"text":"continue"}))
}

pub fn input() -> InputEnvelope {
    InputEnvelope {
        schema_version: 1,
        sid: owner().sid,
        tid: Tid("task-a".into()),
        input_id: InputId("input-a".into()),
        business_digest: Digest("input-digest".into()),
        body: InputBody::User { payload: payload() },
    }
}

pub fn receipt() -> AdmissionReceipt {
    let input = input();
    AdmissionReceipt {
        sid: input.sid,
        tid: input.tid,
        input_id: input.input_id,
        business_digest: input.business_digest,
    }
}

pub fn admission() -> AdmitRequest {
    AdmitRequest {
        input: input(),
        create_session: Some(spec()),
        create_task: Some(snapshot()),
    }
}

pub fn content() -> TaskContent {
    TaskContent {
        status: TaskStatus::Working,
        interactions: vec![],
        snapshot: payload(),
        result_ref: None,
    }
}

pub fn event_id() -> EventId {
    EventId {
        commit: CommitSeq(8),
        index: NonZeroU32::MIN,
    }
}

pub fn task_view() -> TaskView {
    TaskView {
        scope: scope(),
        sid: owner().sid,
        tid: input().tid,
        content: content(),
        covered_through: Some(event_id()),
    }
}

pub fn snapshot() -> TaskSnapshot {
    TaskSnapshot {
        schema_version: 1,
        scope: scope(),
        sid: owner().sid,
        tid: input().tid,
        content: content(),
        output_limits: PHASE_ONE_OUTPUT_LIMITS,
        output_usage: OutputUsage::default(),
        snapshot_event_id: Some(event_id()),
        source_commit: CommitSeq(8),
        created_at: TimestampMillis(100),
        updated_at: TimestampMillis(200),
    }
}

pub fn cursor() -> TaskCursor {
    TaskCursor {
        sid: owner().sid,
        tid: input().tid,
        token: position().token,
    }
}

pub fn state() -> RunnerState {
    RunnerState {
        format_version: 1,
        value: json!({"step":1}),
    }
}

pub fn checkpoint() -> Checkpoint {
    Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(8),
        consumed: Some(position()),
        tasks: vec![TaskProgress {
            tid: input().tid,
            phase: TaskPhase::Running,
            input: payload(),
            result_ref: None,
            output_limits: PHASE_ONE_OUTPUT_LIMITS,
            output_usage: OutputUsage::default(),
            publication: SnapshotProgress::default(),
            finalization: None,
        }]
        .into(),
        runner_state: Some(TaskRunnerState { tid: input().tid, state: state() }),
        dispatch_seq: Default::default(),
        deadlines: vec![],
        view_drafts: Default::default(),
        last_event_id: Some(event_id()),
        outbox: Some(CommittedBatch {
            commit_id: proof().commit_id,
            source_commit: CommitSeq(8),
            content_digest: Digest("batch-digest".into()),
            effects: vec![Effect {
                id: source().effect_id,
                content_digest: source().content_digest,
                kind: EffectKind::PublishSnapshot {
                    snapshot: snapshot(),
                },
            }],
        }),
    }
}

pub fn target() -> AgentBinding {
    AgentBinding {
        agent: AgentId("agent-a".into()),
        binding_version: BindingVersion("v2".into()),
        credential_ref: CredentialRef("credential-reference".into()),
    }
}

pub fn capabilities() -> DownstreamCapabilities {
    DownstreamCapabilities {
        idempotent_start: true,
        idempotent_input: true,
        query_by_request: true,
        query_by_task: true,
        subscribe: true,
        resume_events: true,
        cancel: true,
    }
}

pub fn spec() -> SessionSpec {
    SessionSpec {
        schema_version: 1,
        sid: owner().sid,
        shard: owner().shard,
        scope: scope(),
        kind: SessionKind::FsmGroup,
        group: GroupId("group-a".into()),
        workflow: WorkflowRef {
            definition_id: "workflow-a".into(),
            revision: "2".into(),
            digest: Digest("workflow-digest".into()),
        },
        participants: vec![ParticipantBinding {
            node: NodeId("node-a".into()),
            target: target(),
            pool: PoolId("pool-a".into()),
            capabilities: capabilities(),
        }],
        limits: SessionLimits {
            max_queued_tasks: 10,
            max_task_steps: 100,
            task_timeout_millis: 60_000,
            max_inline_bytes: 4096,
        },
        digest: Digest("spec-digest".into()),
        created_at: TimestampMillis(100),
    }
}

pub fn workflow() -> bcs_domain::collaboration::StateMachineDefinition {
    serde_json::from_value(json!({})).expect("workflow fixture")
}

pub fn dispatch_view() -> DispatchView {
    DispatchView {
        spec: DispatchSpec {
            schema_version: 1,
            sid: owner().sid,
            tid: input().tid,
            did: Did("dispatch-a".into()),
            node: NodeId("node-a".into()),
            attempt: 1,
            target: target(),
            pool: PoolId("pool-a".into()),
            capabilities: capabilities(),
            request: payload(),
            deadline: TimestampMillis(60_100),
        },
        control: DispatchControlState {
            cancel: None,
            pending_input: vec![],
        },
        execution: DispatchExecution {
            holder: Some(holder()),
            phase: DispatchPhase::Accepted,
            remote: Some(remote()),
            confirmed_input_through: Some(InputSeq(1)),
            cancellation: CancelObservation::NotSent,
            outcome: None,
        },
    }
}

pub fn holder() -> Holder {
    Holder {
        instance: instance(),
        epoch: HolderEpoch(3),
    }
}

pub fn remote() -> RemoteTaskRef {
    RemoteTaskRef {
        task_id: "remote-a".into(),
        context_id: None,
    }
}

pub fn final_record() -> FinalTaskRecord {
    FinalTaskRecord {
        schema_version: 1,
        scope: scope(),
        sid: owner().sid,
        tid: input().tid,
        status: TerminalStatus::Completed,
        snapshot: payload(),
        result: payload(),
        source_commit: CommitSeq(8),
        finalized_at: TimestampMillis(300),
    }
}

pub fn blob() -> BlobRef {
    BlobRef {
        key: "result-a".into(),
        digest: Digest("content-digest".into()),
    }
}
