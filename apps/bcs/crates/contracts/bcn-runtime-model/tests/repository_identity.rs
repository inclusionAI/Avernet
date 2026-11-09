use bcn_runtime_model::*;
#[test]
fn committed_effect_reference_is_stable_scoped_and_rejects_foreign_intent() {
    let scope = Scope::new("test", "tenant", "runtime");
    let session = SessionId::new(scope.clone(), "session/with:separators");
    let intent = ResultIntent { result_id: ResultId::new(scope.clone(), "r"), task_id: TaskId::new(scope.clone(), "t"), node_id: None, attempt: 0, status: TaskStatus::Completed, output: vec![], error: None };
    let effect = EffectIntent::SaveResult { intent: intent.clone() };
    let id = effect_command_id(&session, &effect).unwrap();
    assert_eq!(id.scope, scope); assert_eq!(id.value.len(), 64);
    let reference = CommittedEffectRef { session_id: session.clone(), command_id: id.clone() };
    let bytes = serde_json::to_vec(&reference).unwrap();
    assert_eq!(serde_json::from_slice::<CommittedEffectRef>(&bytes).unwrap(), reference);
    assert_eq!(effect_command_id(&session, &serde_json::from_slice::<EffectIntent>(&serde_json::to_vec(&effect).unwrap()).unwrap()).unwrap(), id);
    let other = SessionId::new(scope.clone(), "other-session");
    assert_ne!(effect_command_id(&other, &effect).unwrap(), id);
    let foreign = SessionId::new(Scope::new("test", "other", "runtime"), session.value);
    assert!(effect_command_id(&foreign, &effect).is_err());
    assert!(session_shard(&other).number < 1024);
}
#[test]
fn connector_projection_cannot_supply_task_status_or_admission_metadata() {
    let projection = ConnectorProjection { task_id: TaskId::new(Scope::new("test", "tenant", "runtime"), "task"), output: vec![MessagePart::Text { text: "node output".into() }], expected_cursor: EventCursor { epoch: 7, sequence: 5 } };
    let json = serde_json::to_value(&projection).unwrap();
    assert!(json.get("task").is_none());
    assert_eq!(serde_json::from_value::<ConnectorProjection>(json).unwrap(), projection);
}
