use super::*;
use crate::sse;
use bcs_protocol::stream::ToolData;

fn user() -> Message { Message::new("u-1", Role::User, vec![Part::text("fix it")]) }

fn kinds(events: &[StreamResponse]) -> Vec<String> {
    events.iter().map(|event| match event {
        StreamResponse::Task(task) => format!("task:{:?}", task.status.state),
        StreamResponse::Message(_) => "message".into(),
        StreamResponse::StatusUpdate(update) => format!("status:{:?}:{}", update.status.state,
            update.metadata.as_ref().and_then(|m| m.get(meta::KIND)).and_then(Value::as_str).unwrap_or("-")),
        StreamResponse::ArtifactUpdate(update) => format!("artifact:{}:{}:{}", update.artifact.text(), update.append, update.last_chunk),
    }).collect()
}

#[test]
fn codex_commentary_is_progress_and_final_answer_is_the_result() {
    let mut p = Projection::new("t-1", "c-1", user());
    assert_eq!(kinds(&p.start()), ["task:Submitted", "status:Working:-"]);
    let mut events = Vec::new();
    events.extend(p.apply(&sse::chat_delta_segment("r", "Checking ", Some("i1"), Some("commentary"))));
    events.extend(p.apply(&sse::chat_delta_segment("r", "the PR", Some("i1"), Some("commentary"))));
    events.extend(p.apply(&sse::agent_tool("r", serde_json::from_value::<ToolData>(serde_json::json!({"phase":"start","name":"grep","toolCallId":"c1"})).unwrap())));
    events.extend(p.apply(&sse::chat_delta_segment("r", "Fixed ", Some("i2"), Some("final_answer"))));
    events.extend(p.apply(&sse::chat_delta_segment("r", "X.", Some("i2"), Some("final_answer"))));
    events.extend(p.apply(&sse::chat_final("r", "Fixed X.".into())));
    assert_eq!(kinds(&events), [
        "status:Working:commentary.delta", "status:Working:commentary.delta",
        "status:Working:commentary", "status:Working:tool",
        "artifact:Fixed :false:false", "artifact:X.:true:false",
        "artifact:Fixed X.:false:true", "status:Completed:-", "task:Completed",
    ]);
    let task = p.task();
    assert_eq!(task.artifacts.len(), 1);
    assert_eq!(task.artifacts[0].text(), "Fixed X.");
    let history: Vec<_> = task.history.iter().map(|m| (m.role, m.text())).collect();
    assert_eq!(history, [(Role::User, "fix it".to_owned()), (Role::Agent, "Checking the PR".to_owned())]);
    // Terminal tasks ignore late events.
    assert!(p.apply(&sse::chat_delta("r", "late")).is_empty());
}

#[test]
fn untagged_deltas_stream_into_artifact_and_final_text_replaces_them() {
    let mut p = Projection::new("t-1", "c-1", user());
    p.start();
    p.apply(&sse::chat_delta("r", "Let me look. "));
    p.apply(&sse::chat_delta("r", "Answer"));
    assert_eq!(p.task().artifacts[0].text(), "Let me look. Answer");
    let events = p.apply(&sse::chat_final("r", "Answer".into()));
    assert_eq!(kinds(&events)[0], "artifact:Answer:false:true");
    assert_eq!(p.task().artifacts[0].text(), "Answer");
}

#[test]
fn interactions_toggle_input_required_until_all_are_resolved() {
    let mut p = Projection::new("t-1", "c-1", user());
    p.start();
    let exec = serde_json::json!({"title":"Bash","command":"rm -rf build","options":[{"decision":"allow-once"},{"decision":"deny"}]});
    let first = p.apply(&sse::interaction_event("r", InteractionPhase::Requested, InteractionKind::Exec, "int-1", exec));
    p.apply(&sse::interaction_event("r", InteractionPhase::Requested, InteractionKind::AskUser, "int-2",
        serde_json::json!({"questions":[{"questionId":"q","question":"Which env?","options":[{"label":"dev"},{"label":"prod"}]}]})));
    let StreamResponse::StatusUpdate(update) = &first[0] else { panic!("status") };
    assert_eq!(update.status.state, TaskState::InputRequired);
    let prompt = update.status.message.as_ref().unwrap().text();
    assert!(prompt.contains("rm -rf build") && prompt.contains("allow-once, deny"), "{prompt}");
    assert_eq!(update.metadata.as_ref().unwrap()[meta::INTERACTION_ID], "int-1");
    assert_eq!(p.pending_interactions(), ["int-1", "int-2"]);
    let pending = &p.task().metadata.as_ref().unwrap()[PENDING_INTERACTIONS];
    assert_eq!(pending[0][meta::INTERACTION_ID], "int-1");
    assert_eq!(pending[1][meta::INTERACTION_KIND], "ask_user");
    let resolved = p.apply(&sse::interaction_event("r", InteractionPhase::Resolved, InteractionKind::Exec, "int-1", serde_json::json!({"decision":"deny"})));
    assert_eq!(kinds(&resolved), ["status:InputRequired:interaction.resolved"]);
    let resolved = p.apply(&sse::interaction_event("r", InteractionPhase::Resolved, InteractionKind::AskUser, "int-2", serde_json::json!({"action":"submit"})));
    assert_eq!(kinds(&resolved), ["status:Working:interaction.resolved"]);
    assert!(p.task().metadata.is_none(), "resolved interactions leave no pending metadata");
}

#[test]
fn errors_aborts_and_silent_ends_are_terminal() {
    let mut p = Projection::new("t-1", "c-1", user());
    p.start();
    let events = p.apply(&sse::chat_error("r", "boom", Some("deadline")));
    assert_eq!(kinds(&events), ["status:Failed:-", "task:Failed"]);
    let StreamResponse::StatusUpdate(update) = &events[0] else { panic!() };
    assert_eq!(update.metadata.as_ref().unwrap()[meta::ERROR_CODE], "deadline");
    assert_eq!(update.status.message.as_ref().unwrap().text(), "boom");

    let mut p = Projection::new("t-2", "c-1", user());
    p.start();
    assert_eq!(kinds(&p.apply(&sse::chat_aborted("r", "user_cancelled"))), ["status:Canceled:-", "task:Canceled"]);

    let mut p = Projection::new("t-3", "c-1", user());
    p.start();
    assert_eq!(kinds(&p.finish_without_terminal("bridge stopped")), ["status:Failed:-", "task:Failed"]);
}

#[test]
fn history_length_trims_oldest_messages() {
    let mut p = Projection::new("t-1", "c-1", user());
    p.push_history(Message::new("a", Role::Agent, vec![Part::text("1")]));
    p.push_history(Message::new("b", Role::Agent, vec![Part::text("2")]));
    assert_eq!(with_history_length(p.task().clone(), Some(0)).history.len(), 0);
    assert_eq!(with_history_length(p.task().clone(), Some(1)).history[0].message_id, "b");
    assert_eq!(with_history_length(p.task().clone(), None).history.len(), 3);
}
