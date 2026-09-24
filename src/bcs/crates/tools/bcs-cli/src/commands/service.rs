//! Service command execution.
use super::*;

pub(super) async fn execute_service(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Service {
            token,
            command,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, cli_json, .. } = command_context;

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
                    baas_session_id,
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
                    let meta_json = merge_baas_session_id_into_meta(
                        meta_json,
                        baas_session_id.as_deref(),
                    )
                    .map_err(|e| anyhow!("--baas-session-id: {}", e))?;

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
                        if cli_json {
                            println!("{}", serde_json::to_string(&result)?);
                        } else {
                            print_service_session_summary(&result, "Invocation submitted");
                        }
                    } else {
                        let budget = timeout_ms.unwrap_or(1_800_000);
                        let final_session = wait_for_service_completion(
                            &client, &group, &sid, budget,
                        )
                        .await?;
                        if cli_json {
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

                    if cli_json {
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
                    let final_session = wait_for_service_completion(
                        &client, gid, sid_ref, budget,
                    )
                    .await?;
                    if cli_json {
                        println!("{}", serde_json::to_string(&final_session)?);
                    } else {
                        print_service_session_summary(&final_session, "Service session");
                    }
                }
            }

    Ok(())
}
