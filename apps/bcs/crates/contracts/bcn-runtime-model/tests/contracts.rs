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
