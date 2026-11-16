use bcn_runtime_model::*;

#[test]
fn durable_handle_roundtrip_preserves_scope_and_binding_without_secrets() {
    let handle = ExecutionHandle {
        target: AgentId::new(Scope::new("local", "tenant-a", "runtime-v1"), "agent-1"),
        binding_version: "v7".into(), remote_task_id: Some("remote-42".into()),
        remote_context_id: Some("context-8".into()),
    };
    let value = serde_json::to_value(&handle).unwrap();
    assert_eq!(value["target"]["scope"]["namespace"], "runtime-v1");
    assert_eq!(value["binding_version"], "v7");
    assert_eq!(serde_json::from_value::<ExecutionHandle>(value).unwrap(), handle);
}
#[test]
fn task_status_does_not_publish_pending_result_as_terminal() {
    assert!(!TaskStatus::PendingResult.is_terminal());
    assert!(!TaskStatus::InputRequired.is_terminal());
    for status in [TaskStatus::Completed, TaskStatus::Failed, TaskStatus::Cancelled] {
        assert!(status.is_terminal());
    }
}
#[test]
fn namespace_validation_fails_closed() {
    for namespace in ["", "../old", "a/b", "a:b", " a", "a\n", "é"] {
        assert!(Scope::new("dev", "tenant", namespace).validate().is_err());
    }
    assert!(Scope::new("dev", "tenant", "runtime-v1").validate().is_ok());
}

fn execution_binding() -> FrozenTargetBinding {
    FrozenTargetBinding {
        target: AgentId::new(Scope::new("local", "tenant-a", "runtime-v1"), "agent"),
        binding_version: "v7".into(), binding_id: "binding-7".into(),
        capabilities: ConnectorCapabilities { streaming: true, query: true, subscribe: true,
            input_continuation: true, cancellation: CancellationSupport::SingleTask,
            idempotency: IdempotencySupport::Unsupported },
        credential_ref: Some(CredentialRef { name: "agent-credential".into(), version: "v2".into() }),
    }
}

#[test]
fn send_and_cancel_operations_keep_distinct_ids_and_payloads_through_recovery() {
    let scope = Scope::new("local", "tenant-a", "runtime-v1");
    let binding = execution_binding();
    let handle = ExecutionHandle { target: binding.target.clone(), binding_version: "v7".into(),
        remote_task_id: Some("remote-task".into()), remote_context_id: Some("remote-context".into()) };
    let send = DispatchOperation::Send { request: AgentSendRequest {
        dispatch_id: DispatchId::new(scope.clone(), "send-dispatch"),
        operation_id: OperationId::new(scope.clone(), "send-operation"), binding: binding.clone(),
        message: Message { message_id: MessageId::new(scope.clone(), "message"), context_id: None,
            task_id: None, parts: vec![MessagePart::Text { text: "hello".into() }] },
        handle: None, expires_at_ms: None,
    } };
    let cancel = DispatchOperation::Cancel {
        dispatch_id: DispatchId::new(scope.clone(), "cancel-dispatch"), binding,
        request: AgentCancelRequest { operation_id: OperationId::new(scope.clone(), "cancel-operation"),
            handle: handle.clone(), exclusive_context: false },
    };
    assert_ne!(send.dispatch_id(), cancel.dispatch_id());
    assert_ne!(send.operation_id(), cancel.operation_id());
    for operation in [send, cancel.clone()] {
        let receipt = match &operation {
            DispatchOperation::Send { .. } => DispatchReceipt::Agent { event: AgentEvent::Accepted { handle: handle.clone() } },
            DispatchOperation::Cancel { .. } => DispatchReceipt::Cancellation {
                outcome: CancelOutcome::Confirmed { affected_remote_task_ids: vec!["remote-task".into()] } },
        };
        let record = DispatchRecord { context: DispatchContext { command_id: CommandId::new(scope.clone(), "command"), session_id: SessionId::new(scope.clone(), "session"), task_id: TaskId::new(scope.clone(), "task"), node_id: Some("node".into()) }, fence: DispatchFence { dispatch_id: operation.dispatch_id().clone(),
            owner: NodeIdentity { scope: scope.clone(), node_id: "connector".into(), incarnation: "process-1".into() }, epoch: 3 },
            revision: Revision(8), operation, phase: DispatchPhase::MayHaveSent,
            handle: Some(handle.clone()), receipt: Some(receipt), updated_at_ms: 42 };
        let recovered: DispatchRecord = serde_json::from_slice(&serde_json::to_vec(&record).unwrap()).unwrap();
        assert_eq!(recovered, record);
        assert_eq!(recovered.fence.dispatch_id, *recovered.operation.dispatch_id());
    }
    let encoded = serde_json::to_value(cancel).unwrap();
    assert_eq!(encoded["kind"], "cancel");
    assert_eq!(encoded["request"]["operation_id"]["value"], "cancel-operation");
    assert_eq!(encoded["request"]["handle"]["remote_task_id"], "remote-task");
    assert_eq!(encoded["binding"]["credential_ref"]["version"], "v2");
}

#[test]
fn cancellation_receipts_and_inputs_preserve_unknown_and_confirmation_evidence() {
    let scope = Scope::new("local", "tenant-a", "runtime-v1");
    for outcome in [CancelOutcome::Confirmed { affected_remote_task_ids: vec!["remote-task".into()] },
        CancelOutcome::AlreadyTerminal, CancelOutcome::Unknown] {
        let receipt = DispatchReceipt::Cancellation { outcome: outcome.clone() };
        let recovered: DispatchReceipt = serde_json::from_slice(&serde_json::to_vec(&receipt).unwrap()).unwrap();
        assert_eq!(recovered, receipt);
        let binding = execution_binding();
        let dispatch_id = DispatchId::new(scope.clone(), "cancel-dispatch");
        let operation_id = OperationId::new(scope.clone(), "cancel-operation");
        let handle = ExecutionHandle { target: binding.target.clone(), binding_version: "v7".into(),
            remote_task_id: Some("remote-task".into()), remote_context_id: Some("remote-context".into()) };
        let record = DispatchRecord { context: DispatchContext { command_id: CommandId::new(scope.clone(), "command"), session_id: SessionId::new(scope.clone(), "session"), task_id: TaskId::new(scope.clone(), "task"), node_id: Some("node".into()) },
            fence: DispatchFence { dispatch_id: dispatch_id.clone(), owner: NodeIdentity {
                scope: scope.clone(), node_id: "connector".into(), incarnation: "process-2".into() }, epoch: 4 },
            revision: Revision(9), operation: DispatchOperation::Cancel { dispatch_id, binding,
                request: AgentCancelRequest { operation_id, handle: handle.clone(), exclusive_context: true } },
            phase: DispatchPhase::ResultRecorded, handle: Some(handle), receipt: Some(receipt), updated_at_ms: 43,
        };
        assert_eq!(serde_json::from_slice::<DispatchRecord>(&serde_json::to_vec(&record).unwrap()).unwrap(), record);
        let input = SessionInput { event_id: EventId::new(scope.clone(), "cancel-result"), at_ms: 43,
            kind: SessionInputKind::Cancellation { task_id: TaskId::new(scope.clone(), "task"), node_id: None,
                dispatch_id: DispatchId::new(scope.clone(), "cancel-dispatch"),
                operation_id: OperationId::new(scope.clone(), "cancel-operation"), outcome } };
        assert_eq!(serde_json::from_slice::<SessionInput>(&serde_json::to_vec(&input).unwrap()).unwrap(), input);
    }
}

#[test]
fn committed_hard_event_reference_needs_only_session_and_event_identity() {
    let scope = Scope::new("local", "tenant-a", "runtime-v1");
    let reference = CommittedEventRef { session_id: SessionId::new(scope.clone(), "human-first-session"),
        event_id: EventId::new(scope, "input-required") };
    let encoded = serde_json::to_value(&reference).unwrap();
    assert_eq!(encoded.as_object().unwrap().len(), 2);
    assert_eq!(encoded["event_id"]["value"], "input-required");
    assert_eq!(serde_json::from_value::<CommittedEventRef>(encoded).unwrap(), reference);
}
