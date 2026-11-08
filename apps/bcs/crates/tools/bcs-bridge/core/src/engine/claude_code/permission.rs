use super::*;

/// Build the kind-specific `extra` payload for the `interaction/requested` SSE
/// event (per spec §5.2).
///
/// - exec (`tool_name != "AskUserQuestion"`): `{title, options}` plus either
///   `command` (when `input.command` is a non-empty string) or `description`
///   (otherwise). BCS validates that a present `command` must be a non-empty
///   string — a present-but-null/empty command drops the interaction and parks
///   the run forever, so a missing/non-string command MUST NOT emit the key;
///   we synthesize a human-readable `description` instead. The fixed options
///   `[allow-once, deny]` are always present.
/// - ask_user (`AskUserQuestion`): `{questions}` from `input.questions[]` —
///   questionId = `header` fallback `question_N`, options `label → {label,
///   value=label}` (cc has no separate value; baas-fallback parity).
pub(super) fn build_requested_extra(tool_name: &str, input: &Value) -> Value {
    if tool_name == "AskUserQuestion" {
        json!({ "questions": map_ask_user_questions(input) })
    } else {
        // Claude Code ends plan mode through the ExitPlanMode tool: the run asks
        // the human to confirm moving from read-only planning into execution.
        // Present that as a readable confirmation rather than a bare tool name;
        // the tool's `input.plan` already carries the proposal the approver reads.
        let plan_mode_exit = matches!(tool_name, "ExitPlanMode" | "exit_plan_mode");
        let title = if plan_mode_exit { "Exit plan mode" } else { tool_name };
        // Only emit `command` when it is a non-empty string; otherwise omit the
        // key (BCS rejects present-but-null/empty command) and synthesize a
        // human-readable `description` so the exec interaction still carries
        // context for the approver.
        let command = input
            .get("command")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty());
        let mut extra = json!({
            "title": title,
            "options": [
                { "decision": "allow-once", "label": "Allow once" },
                { "decision": "deny", "label": "Deny" },
            ],
        });
        match command {
            Some(cmd) => extra["command"] = json!(cmd),
            None => {
                let plan = plan_mode_exit
                    .then(|| input.get("plan").and_then(Value::as_str))
                    .flatten()
                    .filter(|s: &&str| !s.trim().is_empty());
                let description = plan
                    .map(str::to_string)
                    .unwrap_or_else(|| synthesize_exec_description(input));
                extra["description"] = json!(description);
            }
        }
        extra
    }
}

/// Synthesize a human-readable `description` for an exec interaction whose tool
/// input carries no usable `command` string. Prefer `path`/`file_path` (the
/// common Read/Edit/Grep shapes); fall back to a compact JSON rendering of the
/// input, truncated to 200 chars on a UTF-8 character boundary (never byte-slice
/// — the input may contain multi-byte text). `serde_json::to_string` on a
/// `Value` cannot fail, but `unwrap_or_default` keeps this panic-free.
pub(super) fn synthesize_exec_description(input: &Value) -> String {
    if let Some(p) = input
        .get("path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        return p.to_string();
    }
    if let Some(p) = input
        .get("file_path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    {
        return p.to_string();
    }
    const MAX_LEN: usize = 200;
    let compact = serde_json::to_string(input).unwrap_or_default();
    match compact.char_indices().nth(MAX_LEN) {
        Some((idx, _)) => compact[..idx].to_string(),
        None => compact,
    }
}

/// Map cc `AskUserQuestion` `input.questions[]` to BCN questions. See
/// [`build_requested_extra`]: `questionId` 取 `header`，缺省回落到
/// `question_N`（1-based 索引）；`options[].value` 以 `label` 回填（cc 无独立
/// value，对齐 baas fallback 策略）；`multiSelect` 透传。**注意**：含
/// `secret`/`isSecret` 标记的问题在本函数之前已被 [`ask_user_has_secret`]
/// 拦截并应答 deny，因此不会走到本映射。
pub(super) fn map_ask_user_questions(input: &Value) -> Value {
    let Some(questions) = input.get("questions").and_then(|q| q.as_array()) else {
        return json!([]);
    };
    let out: Vec<Value> = questions
        .iter()
        .enumerate()
        .map(|(i, q)| {
            let question_id = q
                .get("header")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .unwrap_or_else(|| format!("question_{}", i + 1));
            let question = q.get("question").and_then(|v| v.as_str()).unwrap_or("");
            let options = q
                .get("options")
                .and_then(|o| o.as_array())
                .map(|arr| {
                    arr.iter()
                        .map(|o| {
                            let label = o.get("label").and_then(|v| v.as_str()).unwrap_or("");
                            json!({ "label": label, "value": label })
                        })
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            let mut obj = json!({
                "questionId": question_id,
                "question": question,
                "options": options,
            });
            if let Some(ms) = q.get("multiSelect").and_then(|v| v.as_bool()) {
                obj["multiSelect"] = json!(ms);
            }
            obj
        })
        .collect();
    json!(out)
}

/// Refuse conversion when any `AskUserQuestion` question carries a `secret`
/// (spec §5.2): BCS interaction cannot ferry secret answers — the driver
/// answers the engine `deny` directly and emits no interaction. Checks both
/// the question-level `secret`/`isSecret` boolean and (defensively) a
/// whole-call `isSecret` flag.
pub(super) fn ask_user_has_secret(input: &Value) -> bool {
    if input.get("secret").and_then(|v| v.as_bool()) == Some(true)
        || input.get("isSecret").and_then(|v| v.as_bool()) == Some(true)
    {
        return true;
    }
    let Some(questions) = input.get("questions").and_then(|q| q.as_array()) else {
        return false;
    };
    questions.iter().any(|q| {
        q.get("secret").and_then(|v| v.as_bool()) == Some(true)
            || q.get("isSecret").and_then(|v| v.as_bool()) == Some(true)
    })
}

/// Map a BCS resolution value to the cc control_response `behavior`
/// (`"allow"`/`"deny"`).
///
/// For exec, only the offered `allow-once` decision maps to `allow`.
/// Any other value, including `allow_once`, maps to `deny`; decision strings
/// are exact wire values, not aliases. We never infer allow from an unknown
/// decision.
///
/// ask_user follows the BCS workbench contract (`bcs-interaction`
/// `validate_ask_user_resolution`): the only actions it sends are
/// `submit` (with a questionId -> {values:[...]} answers object; BCS has
/// already validated one entry per question, where empty `values` is an
/// explicit skip) and `cancel`. cc v1 has no answers channel, so both
/// collapse to allow/deny (`spec §5.2`); `submit` allows, everything else
/// denies.
pub(super) fn resolution_to_behavior(resolution: &Value) -> &'static str {
    if let Some(decision) = resolution["decision"].as_str() {
        return match decision {
            "allow-once" => "allow",
            _ => "deny",
        };
    }
    match resolution["action"].as_str() {
        // A valid submission answers every question; BCS rejects an empty
        // answers map (and every values entry) before the delivery here.
        Some("submit") if resolution["answers"].as_object().is_some_and(|a| !a.is_empty()) => "allow",
        _ => "deny",
    }
}

/// A submitted ask_user form is denied while its `message` carries the human's
/// completed answers, phrased so the model adopts them and continues without
/// re-asking.
///
/// The stream-json permission protocol has NO answers channel for an allowed
/// AskUserQuestion: the CLI validates `updatedInput` against the tool schema
/// (rejecting the workbench's `{question -> {values}}` shape outright) and,
/// when validation passes, still executes the tool as an unanswered headless
/// form — the model then sees "The user did not answer the questions" and
/// falls back to re-asking in plain chat text. The one part of a denial that
/// does reach the model is its message, rendered into the tool result.
///
/// Denial is cosmetically an error to the CLI (`is_error: true` on the tool
/// result). [`ANSWERED_ASK_USER_MARK`] prefixes this message so the
/// transcription below lifts that flag for exactly these results — the model
/// reads the answers, the workbench sees a normal tool row.
pub(super) const ANSWERED_ASK_USER_MARK: &str = "The user answered this question card";
const ANSWERED_ASK_USER_PREFIX: &str = "The user answered this question card; adopt these answers and continue the task without asking again:";

pub(super) fn answered_ask_user_response(request_id: &str, resolution: &Value) -> Value {
    let message = if let Some(answers) = resolution.get("answers").and_then(Value::as_object) {
        let mut lines = String::from(ANSWERED_ASK_USER_PREFIX);
        for (question, answer) in answers {
            let values = answer
                .get("values")
                .and_then(Value::as_array)
                .map(|values| {
                    values
                        .iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join("; ")
                })
                .unwrap_or_default();
            if values.is_empty() {
                lines.push_str(&format!("\n- {question}: (unanswered)"));
            } else {
                lines.push_str(&format!("\n- {question}: {values}"));
            }
        }
        lines
    } else {
        "The user submitted the question card without answers; continue with the task."
            .to_owned()
    };
    json!({
        "type": "control_response",
        "response": { "subtype": "success", "request_id": request_id,
                      "response": { "behavior": "deny", "message": message } }
    })
}

/// A successful control exchange may carry either an allow or a deny decision.
/// Claude requires the outer subtype and a message on the deny branch;
/// updatedInput belongs only to an allow response.
pub(super) fn permission_control_response(request_id: &str, behavior: &str, input: &Value) -> Value {
    let permission = if behavior == "allow" {
        json!({ "behavior": "allow", "updatedInput": input })
    } else {
        json!({ "behavior": "deny", "message": "Tool use was not approved." })
    };
    json!({
        "type": "control_response",
        "response": { "subtype": "success", "request_id": request_id,
                      "response": permission }
    })
}

/// Drive one `can_use_tool` control request as a HITL interaction (Task 12):
///
/// 1. (ask_user only) refuse secret-marked questions — answer the engine `deny`
///    immediately and log; no interaction is emitted (spec §5.2).
/// 2. register a pending interaction with the registry → mint interactionId.
/// 3. emit `interaction/requested` (exec: title/command/options; ask_user:
///    questions).
/// 4. await the BCS resolution (or abort → deny fallback).
/// 5. write the cc `control_response` back to the engine's control channel
///    (`behavior` allow/deny; `updatedInput` only on allow).
/// 6. emit `interaction/resolved`.
///
/// Returns `Ok(())` so the `run_turn` loop continues to the next line; an IO
/// failure writing the control_response is returned as `TurnError::Io`, and a
/// failed `requested` send (BCS disconnect) aborts the engine.
pub(super) async fn handle_control_request(
    cli: &mut CliSession,
    events: &tokio::sync::mpsc::Sender<StreamEvent>,
    abort: &tokio_util::sync::CancellationToken,
    req: &TurnRequest,
    request_id: String,
    tool_name: String,
    input: Value,
) -> Result<(), TurnError> {
    let kind = if tool_name == "AskUserQuestion" {
        InteractionKind::AskUser
    } else {
        InteractionKind::Exec
    };
    // 1. Secret-marked ask_user questions are refused (spec §5.2): the engine
    //    gets deny and no interaction is emitted to BCS.
    if kind == InteractionKind::AskUser && ask_user_has_secret(&input) {
        tracing::warn!(
            target: "bcs_bridge_core",
            request_id = %request_id, tool = %tool_name,
            "AskUserQuestion with secret-marked question refused; answering deny"
        );
        let deny = permission_control_response(&request_id, "deny", &input);
        cli.write_line(&deny.to_string()).await.map_err(TurnError::Io)?;
        return Ok(());
    }
    // 2-3. Register + emit requested.
    let (iid, resolution_rx) = req.interactions.register(&req.run_id, kind, request_id.clone());
    let requested = build_requested_extra(&tool_name, &input);
    if events
        .send(sse::interaction_event(
            &req.run_id,
            InteractionPhase::Requested,
            kind,
            &iid,
            requested,
        ))
        .await
        .is_err()
    {
        cli.kill().await;
        return Err(TurnError::Aborted);
    }
    // 4. Await the BCS resolution; abort or a dropped sender recovers to deny
    //    so the driver never blocks on a dead interaction.
    let resolution = tokio::select! {
        _ = abort.cancelled() => json!({ "decision": "deny" }),
        r = resolution_rx => r.unwrap_or_else(|_| json!({ "decision": "deny" })),
    };
    // 5. Write the control_response with the mapped behavior. An allowed
    //    AskUserQuestion carries the submitted answers in its updatedInput:
    //    the engine reads input.answers as the completed human form, so the
    //    turn continues with the chosen values instead of waiting on a
    //    headless prompt nobody can answer.
    let behavior = resolution_to_behavior(&resolution);
    let response = if kind == InteractionKind::AskUser && behavior == "allow" {
        answered_ask_user_response(&request_id, &resolution)
    } else {
        permission_control_response(&request_id, behavior, &input)
    };
    cli.write_line(&response.to_string()).await.map_err(TurnError::Io)?;
    // 6. Emit resolved (best-effort — BCS may have disconnected post-request).
    // v1 limitation: ask_user resolutions carry no `decision` key, so the
    // resolved event's `decision` is null for ask_user (consistent with the
    // no-answers-channel collapse above).
    let _ = events
        .send(sse::interaction_event(
            &req.run_id,
            InteractionPhase::Resolved,
            kind,
            &iid,
            json!({ "decision": resolution["decision"].clone() }),
        ))
        .await;
    Ok(())
}

