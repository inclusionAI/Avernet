use std::collections::HashMap;

use anyhow::Result;
use bcs_cli::{BcsClient, ChatAsyncError, ChatRunOutcome};
use serde_json::json;
use tracing::debug;

use super::CLI_LOG_TARGET;
use super::Commands;
use super::authentication::create_client;
use super::environment::get_token;
use super::resource_args::ChatRunCommands;


/// Execute a chat command: interactive chat or a chat-run subcommand.
pub(super) async fn execute(
    command: Commands,
    bcs_url: String,
    bcs_cookie: Option<String>,
    oauth_headers: Option<HashMap<String, String>>,
    structured_mode: bool,
    debug: bool,
) -> Result<()> {
    match command {
        Commands::Chat {
            token,
            bot_uuid,
            message,
            timeout_ms,
            session_id,
            tags,
            response_mode,
            poll_wait_ms,
            detach,
            wait_until,
            organization_code,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let client_wait_timeout_ms = timeout_ms.unwrap_or(if detach || wait_until.is_some() {
                60_000
            } else {
                1_800_000
            });
            let json_mode = structured_mode;

            debug_request!(
                debug,
                "POST",
                &format!("/bots/{}/chat-async", &bot_uuid),
                json!({
                    "message": &message,
                    "from": serde_json::Value::Null,
                    "session_id": &session_id,
                    "tags": &tags,
                    "response_mode": &response_mode,
                    "organization_code": &organization_code,
                })
            );
            let outcome: ChatRunOutcome = match client
                .chat_async(
                    &bot_uuid,
                    &message,
                    None,
                    session_id.as_deref(),
                    &tags,
                    response_mode.as_deref(),
                    organization_code.as_deref(),
                    client_wait_timeout_ms,
                    detach || wait_until.is_some(),
                )
                .await
            {
                Ok(submit) => {
                    // Early ack: timestamped plain-text log line. json mode →
                    // stderr (keep stdout as a single JSON object); non-json
                    // mode → stdout, before the Response / Message block.
                    let ack = format!(
                        "{} [chat] submitted run_id={} session_id={}",
                        chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%.3f%:z"),
                        submit.run_id,
                        submit.session_id
                    );
                    if json_mode {
                        use std::io::Write as _;
                        eprintln!("{}", ack);
                        let _ = std::io::stderr().flush();
                    } else {
                        use std::io::Write as _;
                        println!("{}", ack);
                        let _ = std::io::stdout().flush();
                    }

                    if detach {
                        BcsClient::admitted_outcome(&submit)
                    } else if wait_until.is_some() {
                        client
                            .chat_poll_run_until_running(
                                &submit,
                                poll_wait_ms,
                                std::time::Duration::from_millis(client_wait_timeout_ms),
                            )
                            .await?
                    } else {
                        client
                            .chat_poll_run(
                                &submit,
                                poll_wait_ms,
                                std::time::Duration::from_millis(client_wait_timeout_ms),
                            )
                            .await?
                    }
                }
                Err(ChatAsyncError::Transport(msg)) => ChatRunOutcome {
                    delivery: None,
                    delivered: false,
                    submitted: false,
                    run_id: None,
                    session_id: None,
                    bot_uuid: Some(bot_uuid.clone()),
                    state: "submit_indeterminate".to_string(),
                    response_content: None,
                    error_message: Some(format!(
                        "chat_async transport error: {}; run may have been created server-side, ID unknown",
                        msg
                    )),
                    content_truncated: false,
                },
                Err(ChatAsyncError::NotSuccessful { status, body }) => ChatRunOutcome {
                    delivery: None,
                    delivered: false,
                    submitted: false,
                    run_id: None,
                    session_id: None,
                    bot_uuid: Some(bot_uuid.clone()),
                    state: "submit_failed".to_string(),
                    response_content: None,
                    error_message: Some(format!("chat_async failed ({}): {}", status, body)),
                    content_truncated: false,
                },
                Err(ChatAsyncError::InvalidResponse(msg)) => ChatRunOutcome {
                    delivery: None,
                    delivered: false,
                    submitted: false,
                    run_id: None,
                    session_id: None,
                    bot_uuid: Some(bot_uuid.clone()),
                    state: "submit_indeterminate".to_string(),
                    response_content: None,
                    error_message: Some(format!(
                        "chat_async response unreadable: {}; run may have been created server-side, ID unknown",
                        msg
                    )),
                    content_truncated: false,
                },
            };

            debug!(
                target: CLI_LOG_TARGET,
                state = %outcome.state,
                delivered = outcome.delivered,
                "chat outcome"
            );

            let success = if detach {
                outcome.admission_succeeded()
            } else {
                outcome.delivered
            };
            if json_mode {
                // stdout = EXACTLY one JSON object (jq-parseable).
                let json_value = if detach {
                    serde_json::json!({
                        "delivered": outcome.delivered,
                        "submitted": outcome.submitted,
                        "bot_uuid": outcome.bot_uuid,
                        "run_id": outcome.run_id,
                        "session_id": outcome.session_id,
                        "state": outcome.state,
                        "delivery_status": outcome.delivery.as_ref().map(|d| &d.status),
                        "wait_reason": outcome.delivery.as_ref().and_then(|d| d.wait_reason.as_ref()),
                        "error_message": outcome.error_message,
                    })
                } else {
                    serde_json::json!({
                        "delivered": outcome.delivered,
                        "submitted": outcome.submitted,
                        "bot_uuid": outcome.bot_uuid,
                        "run_id": outcome.run_id,
                        "session_id": outcome.session_id,
                        "state": outcome.state,
                        "delivery_status": outcome.delivery.as_ref().map(|d| &d.status),
                        "wait_reason": outcome.delivery.as_ref().and_then(|d| d.wait_reason.as_ref()),
                        "response": {"content": outcome.response_content.unwrap_or_default()},
                        "error_message": outcome.error_message,
                        "content_truncated": outcome.content_truncated,
                    })
                };
                println!("{}", serde_json::to_string(&json_value)?);
            } else {
                // Human text on stdout.
                if outcome.delivered {
                    if detach {
                        println!("Message submitted to {}", bot_uuid);
                    } else {
                        println!("Response from {}:", bot_uuid);
                        let content = outcome.response_content.unwrap_or_default();
                        println!(
                            "{}",
                            serde_json::to_string_pretty(&serde_json::json!({"content": content}))?
                        );
                    }
                }
                println!("Run: {}", outcome.run_id.as_deref().unwrap_or("none"));
                println!(
                    "Session: {}",
                    outcome.session_id.as_deref().unwrap_or("none")
                );
                println!("State: {}", outcome.state);
                if let Some(delivery) = &outcome.delivery {
                    println!("Delivery: {}", delivery.status);
                    if let Some(reason) = &delivery.wait_reason {
                        println!("Waiting: {}", reason);
                    }
                }
                if let Some(err) = &outcome.error_message {
                    println!("Error: {}", err);
                }
            }

            if !success {
                std::process::exit(1);
            }
        }

        Commands::ChatRun { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );
            let result = match command {
                ChatRunCommands::Status { run_id } => {
                    serde_json::to_value(client.chat_run_status(&run_id, None, None).await?)?
                }
                ChatRunCommands::Cancel { run_id } => {
                    serde_json::to_value(client.chat_run_cancel(&run_id).await?)?
                }
            };
            if structured_mode {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("Run: {}", result["run_id"].as_str().unwrap_or("unknown"));
                println!("State: {}", result["state"].as_str().unwrap_or("unknown"));
                if let Some(status) = result
                    .get("delivery")
                    .and_then(|d| d.get("status"))
                    .and_then(|v| v.as_str())
                {
                    println!("Delivery: {}", status);
                    if matches!(status, "cancelling" | "cancel_unknown") {
                        println!("Cancellation is not confirmed; query status again.");
                    }
                }
            }
        }

        _ => unreachable!("wrong command family"),
    }

    Ok(())
}
