//! Question forms are parked before native execution; answers resume the same desktop call.
use bcs_bridge_core::{engine::{TurnError, TurnRequest}, sse};
use bcs_protocol::stream::{InteractionKind, InteractionPhase, StreamEvent};
use serde_json::{json, Value};
use std::collections::HashSet;
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

pub(crate) fn deny(reason: &str) -> Value {
    json!({"hookSpecificOutput":{"hookEventName":"PreToolUse",
        "permissionDecision":"deny","permissionDecisionReason":reason}})
}

fn questions(input: &Value) -> Result<Vec<Value>, TurnError> {
    let invalid = || TurnError::Protocol("Invalid or secret QwenWork question form".into());
    if input["secret"] == true || input["isSecret"] == true { return Err(invalid()); }
    let items = input["questions"].as_array().filter(|a| !a.is_empty() && a.len() <= 4).ok_or_else(invalid)?;
    let mut headers = HashSet::new();
    items.iter().enumerate().map(|(index, q)| {
        let header = q["header"].as_str().filter(|s| !s.is_empty() && s.len() <= 256).ok_or_else(invalid)?;
        let question = q["question"].as_str().filter(|s| !s.is_empty() && s.len() <= 8192).ok_or_else(invalid)?;
        if !headers.insert(header) || q["secret"] == true || q["isSecret"] == true { return Err(invalid()); }
        let options = q["options"].as_array().filter(|a| a.len() >= 2 && a.len() <= 32).ok_or_else(invalid)?;
        let mut labels = HashSet::new();
        let options = options.iter().map(|o| {
            let label = o["label"].as_str().filter(|s| !s.is_empty() && s.len() <= 1024).ok_or_else(invalid)?;
            if !labels.insert(label) { return Err(invalid()); }
            Ok(json!({"label":label,"value":label}))
        }).collect::<Result<Vec<_>, TurnError>>()?;
        let multi = q.get("multiSelect").map(|v| v.as_bool().ok_or_else(invalid)).transpose()?.unwrap_or(false);
        Ok(json!({"questionId":format!("question_{}",index+1),"question":question,"options":options,"multiSelect":multi}))
    }).collect()
}

pub(crate) fn permitted(input: &Value) -> bool {
    questions(input).is_ok()
}

fn response(input: &Value, form: &[Value], resolution: &Value) -> Result<Value, TurnError> {
    if resolution["action"] != "submit" { return Ok(deny("User declined the question")); }
    let answers = resolution["answers"].as_object().filter(|a| a.len() == form.len())
        .ok_or_else(|| TurnError::Protocol("Incomplete QwenWork answers".into()))?;
    let mut mapped = serde_json::Map::new();
    for (index, q) in form.iter().enumerate() {
        let id = q["questionId"].as_str().ok_or_else(|| TurnError::Protocol("Question id missing".into()))?;
        let values = answers.get(id).and_then(|a| a["values"].as_array())
            .filter(|a| a.len() <= 32)
            .ok_or_else(|| TurnError::Protocol("Question answers missing".into()))?;
        if !q["multiSelect"].as_bool().unwrap_or(false) && values.len() > 1 {
            return Err(TurnError::Protocol("Multiple values for a single-select question".into()));
        }
        let values = values.iter().map(|v| v.as_str().filter(|s| !s.is_empty() && s.len() <= 8192)
            .ok_or_else(|| TurnError::Protocol("Invalid QwenWork answer value".into())))
            .collect::<Result<Vec<_>, _>>()?;
        // Desktop normalizes headers; the bundled worker consumes full question text.
        // Include both keys so the model actually receives the answer, not merely a card update.
        let value = json!(values.join(", "));
        for key in [input["questions"][index]["header"].as_str().unwrap_or_default(), input["questions"][index]["question"].as_str().unwrap_or_default()] {
            if mapped.get(key).is_some_and(|old| old != &value) {
                return Err(TurnError::Protocol("Conflicting native question answer keys".into()));
            }
            mapped.insert(key.into(), value.clone());
        }
    }
    let mut updated = input.clone();
    updated["answers"] = Value::Object(mapped);
    Ok(json!({"answers":updated["answers"]}))
}

pub(crate) async fn answer(
    req: &TurnRequest,
    hook: &Value,
    events: &mpsc::Sender<StreamEvent>,
    abort: &CancellationToken,
    closed: &CancellationToken,
    reply: tokio::sync::oneshot::Sender<Value>,
    control: (&crate::transport::State, &str),
) -> Result<Value, TurnError> {
    let (state, conversation) = control;
    let input = &hook["tool_input"];
    let form = match questions(input) {
        Ok(form) => form,
        Err(_) => {
            let denial = deny("Invalid or secret question form cannot be forwarded");
            let _ = reply.send(denial.clone());
            return Ok(denial);
        },
    };
    let Some(socket) = &state.options.mcp_socket_path else {
        let denial = deny("Configure mcp_socket_path to answer questions through BCS");
        let _ = reply.send(denial.clone());
        return Ok(denial);
    };
    let (id, rx) = req.interactions.register(&req.run_id, InteractionKind::AskUser, hook["tool_use_id"].to_string());
    super::turn::emit(events, sse::interaction_event(&req.run_id, InteractionPhase::Requested,
        InteractionKind::AskUser, &id, json!({"questions":form})), abort, closed).await?;
    let resolution = tokio::select! {
        biased;
        _ = abort.cancelled() => return Err(TurnError::Aborted),
        _ = events.closed() => return Err(TurnError::Aborted),
        _ = closed.cancelled() => return Err(TurnError::EngineExited("QwenWork channel disconnected".into())),
        _ = tokio::time::sleep(std::time::Duration::from_secs(240)) => return Err(TurnError::Protocol("Question answer deadline exceeded".into())),
        resolution = rx => resolution.map_err(|_| TurnError::Aborted)?,
    };
    let output = response(input, &form, &resolution)?;
    if resolution["action"] != "submit" {
        let _ = reply.send(output.clone());
    } else {
        let session = hook["session_id"].as_str().ok_or_else(|| TurnError::Protocol("Question SDK session missing".into()))?;
        let database = state.options.database_path.clone();
        let bot = state.options.bot_id.clone();
        let session = session.to_owned();
        let conversation = conversation.to_owned();
        let chat = tokio::task::spawn_blocking(move || crate::session::task(&database, &session, &bot, &conversation))
            .await.map_err(|e| TurnError::Protocol(e.to_string()))?
            .map_err(TurnError::Protocol)?;
        if reply.send(json!({"continue":true})).is_err() {
            return Err(TurnError::Protocol("Question Hook disconnected before release".into()));
        }
        // While this actor awaits the native answer, later question Hooks stay
        // parked in its queue, so an old answer cannot reach the next question.
        let submit = crate::desktop_control::answer(socket, &chat, hook["tool_use_id"].as_str().unwrap_or_default(), &output["answers"]);
        tokio::select! {
            biased;
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            _ = events.closed() => return Err(TurnError::Aborted),
            _ = closed.cancelled() => return Err(TurnError::EngineExited("QwenWork channel disconnected".into())),
            result = submit => result?,
        }
    }
    super::turn::emit(events, sse::interaction_event(&req.run_id, InteractionPhase::Resolved,
        InteractionKind::AskUser, &id, json!({"action":resolution["action"]})), abort, closed).await?;
    Ok(if resolution["action"] == "submit" { json!({"continue":true}) } else { output })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]
    use super::*;
    #[test]
    fn native_answer_mapping_and_validation() {
        let input = json!({"questions":[{"header":"Color","question":"Which?","options":[{"label":"Blue"},{"label":"Red"}]}]});
        let form = questions(&input).expect("form");
        let result = response(&input, &form, &json!({"action":"submit","answers":{"question_1":{"values":["custom color"]}}})).expect("answer");
        assert_eq!(result["answers"]["Color"], "custom color");
        assert!(response(&input, &form, &json!({"action":"submit","answers":{}})).is_err());
        assert!(response(&input, &form, &json!({"action":"submit","answers":{"question_1":{"values":["Blue","Red"]}}})).is_err());
        assert_eq!(response(&input, &form, &json!({"action":"cancel"})).expect("deny")["hookSpecificOutput"]["permissionDecision"], "deny");
        let skipped = response(&input, &form, &json!({"action":"submit","answers":{"question_1":{"values":[]}}})).expect("skip");
        assert_eq!(skipped["answers"]["Which?"], "");
        let mut multi = input.clone();
        multi["questions"][0]["multiSelect"] = json!(true);
        let multi_form = questions(&multi).expect("multi form");
        assert_eq!(response(&multi, &multi_form, &json!({"action":"submit","answers":{"question_1":{"values":["Blue","Red"]}}})).expect("multi")["answers"]["Which?"], "Blue, Red");
        let mut secret = input;
        secret["questions"][0]["secret"] = json!(true);
        assert!(questions(&secret).is_err());
    }
}
