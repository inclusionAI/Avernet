use std::collections::HashMap;

use anyhow::{Result, anyhow};
use bcs_cli::BcsClient;
use serde_json::json;

use super::Commands;
use super::authentication::create_client;
use super::environment::get_token;
use super::input::parse_json_arg;
use super::resource_args::ServiceCommands;

/// Split a service session id at its canonical `{group_id}:<session_suffix>` boundary into its
/// `(group_id, session_id)` parts. `group_arg` always wins when supplied;
/// otherwise the colon split must succeed.
pub(super) fn split_service_sid<'a>(sid: &'a str, group_arg: Option<&'a str>) -> Result<(&'a str, &'a str)> {
    if let Some(group) = group_arg {
        return Ok((group, sid));
    }
    match sid.split_once(':') {
        Some((gid, _)) if !gid.is_empty() => Ok((gid, sid)),
        _ => Err(anyhow!(
            "Cannot infer group from session id '{}'. Expected '{{group}}:<session_suffix>' \
             or pass --group explicitly.",
            sid
        )),
    }
}

/// Poll a service-invocation session until it completes or the overall
/// budget runs out. Backoff: 500ms initial, doubles each iteration, capped
/// at 5_000ms. Returns the final session JSON, or an error wrapping the
/// last fetched payload when the timeout fires.
pub(super) async fn wait_for_service_completion(
    client: &BcsClient,
    group_id: &str,
    session_id: &str,
    overall_timeout_ms: u64,
) -> Result<serde_json::Value> {
    let started = std::time::Instant::now();
    let budget = std::time::Duration::from_millis(overall_timeout_ms);
    let mut delay_ms: u64 = 500;

    loop {
        let session = client.service_session_status(group_id, session_id).await?;

        let is_done = session
            .get("status")
            .and_then(|v| v.as_str())
            .map(|s| s == "completed")
            .unwrap_or(false);
        if is_done {
            return Ok(session);
        }

        if started.elapsed() >= budget {
            let payload = serde_json::to_string(&session).unwrap_or_default();
            return Err(anyhow!(
                "Timed out after {} ms waiting for service session {} to complete. Last status: {}",
                overall_timeout_ms,
                session_id,
                payload
            ));
        }

        let remaining = budget.saturating_sub(started.elapsed());
        let sleep_for = std::time::Duration::from_millis(delay_ms).min(remaining);
        tokio::time::sleep(sleep_for).await;
        delay_ms = (delay_ms.saturating_mul(2)).min(5_000);
    }
}

/// Render a service-invocation session for human-readable output. The
/// `header` line precedes the session id (e.g. `"Invocation submitted"`,
/// `"Invocation completed"`, `"Service session"`).
pub(super) fn service_session_summary_lines(session: &serde_json::Value, header: &str) -> Vec<String> {
    let mut lines = Vec::new();
    let sid = session
        .get("session_id")
        .and_then(|v| v.as_str())
        .unwrap_or("?");
    lines.push(format!("{}: {}", header, sid));
    if let Some(group_id) = session.get("group_id").and_then(|v| v.as_str()) {
        lines.push(format!("  Group:    {}", group_id));
    }
    if let Some(status) = session.get("status").and_then(|v| v.as_str()) {
        lines.push(format!("  Status:   {}", status));
    }
    if let Some(kind) = session.get("session_kind").and_then(|v| v.as_str()) {
        lines.push(format!("  Kind:     {}", kind));
    }
    if let Some(reused) = session.get("reused").and_then(|v| v.as_bool()) {
        lines.push(format!("  Reused:   {}", reused));
    }
    let run_id = session
        .get("state_machine_run_id")
        .and_then(|v| v.as_str())
        .or_else(|| {
            session
                .get("state_machine_run")
                .and_then(|v| v.get("run"))
                .and_then(|v| v.get("run_id"))
                .and_then(|v| v.as_str())
        });
    if let Some(run_id) = run_id {
        lines.push(format!("  StateRun: {}", run_id));
    }
    if let Some(run_status) = session
        .get("state_machine_run")
        .and_then(|v| v.get("run"))
        .and_then(|v| v.get("status"))
        .and_then(|v| v.as_str())
    {
        lines.push(format!("  RunStatus: {}", run_status));
    }
    if let Some(output) = session.get("output") {
        if !output.is_null() {
            let pretty = serde_json::to_string_pretty(output).unwrap_or_default();
            // UTF-8 safe truncation per src/bcs/CLAUDE.md
            let preview: &str = match pretty.char_indices().nth(500) {
                Some((idx, _)) => &pretty[..idx],
                None => &pretty,
            };
            lines.push(format!("  Output:   {}", preview));
            if pretty.len() > preview.len() {
                lines.push("            (truncated)".to_string());
            }
        }
    }
    if let Some(err) = session.get("error_message").and_then(|v| v.as_str()) {
        if !err.is_empty() {
            lines.push(format!("  Error:    {}", err));
        }
    }
    lines
}

pub(super) fn print_service_session_summary(session: &serde_json::Value, header: &str) {
    for line in service_session_summary_lines(session, header) {
        println!("{}", line);
    }
}

/// Execute a service invocation command.
pub(super) async fn execute(
    command: Commands,
    bcs_url: String,
    bcs_cookie: Option<String>,
    oauth_headers: Option<HashMap<String, String>>,
    json: bool,
    debug: bool,
) -> Result<()> {
    match command {
        Commands::Service { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                ServiceCommands::Invoke {
                    group,
                    input,
                    meta,
                    session_id,

                    caller_id,
                    title,
                    detach,
                    timeout_ms,
                } => {
                    let input_json = input
                        .as_deref()
                        .map(parse_json_arg)
                        .transpose()
                        .map_err(|e| anyhow!("--input: {}", e))?;
                    let meta_json = meta
                        .as_deref()
                        .map(parse_json_arg)
                        .transpose()
                        .map_err(|e| anyhow!("--meta: {}", e))?;

                    debug_request!(
                        debug,
                        "POST",
                        &format!("/services/{}/sessions", &group),
                        json!({
                            "session_id": &session_id,
                            "caller_id": &caller_id,
                            "session_title": &title,
                            "input": &input_json,
                            "meta": &meta_json,
                        })
                    );

                    let result = client
                        .service_invoke(
                            &group,
                            input_json.as_ref(),
                            session_id.as_deref(),
                            caller_id.as_deref(),
                            title.as_deref(),
                            meta_json.as_ref(),
                        )
                        .await?;

                    debug_response!(debug, "202", &result);

                    let sid = result
                        .get("session_id")
                        .and_then(|v| v.as_str())
                        .ok_or_else(|| anyhow!("Server response missing session_id: {}", result))?
                        .to_string();

                    if detach {
                        if json {
                            println!("{}", serde_json::to_string(&result)?);
                        } else {
                            print_service_session_summary(&result, "Invocation submitted");
                        }
                    } else {
                        let budget = timeout_ms.unwrap_or(1_800_000);
                        let final_session =
                            wait_for_service_completion(&client, &group, &sid, budget).await?;
                        if json {
                            println!("{}", serde_json::to_string(&final_session)?);
                        } else {
                            print_service_session_summary(&final_session, "Invocation completed");
                        }
                    }
                }

                ServiceCommands::Status { sid, group } => {
                    let (gid, sid_ref) = split_service_sid(&sid, group.as_deref())?;
                    debug_request!(
                        debug,
                        "GET",
                        &format!("/services/{}/sessions/{}", gid, sid_ref),
                        json!({})
                    );

                    let result = client.service_session_status(gid, sid_ref).await?;
                    debug_response!(debug, "200", &result);

                    if json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        print_service_session_summary(&result, "Service session");
                    }
                }

                ServiceCommands::Wait {
                    sid,
                    group,
                    timeout_ms,
                } => {
                    let (gid, sid_ref) = split_service_sid(&sid, group.as_deref())?;
                    let budget = timeout_ms.unwrap_or(1_800_000);
                    let final_session =
                        wait_for_service_completion(&client, gid, sid_ref, budget).await?;
                    if json {
                        println!("{}", serde_json::to_string(&final_session)?);
                    } else {
                        print_service_session_summary(&final_session, "Service session");
                    }
                }
            }
        }
        _ => unreachable!("wrong command family"),
    }

    Ok(())
}
