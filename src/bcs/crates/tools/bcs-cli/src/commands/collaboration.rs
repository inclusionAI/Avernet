//! Collaboration command execution.
use super::*;

pub(super) async fn execute_collaboration(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Collaboration { token, command } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, structured_mode, .. } = command_context;
    match command {
            CollaborationCommands::Permission { session } => {
                let token = get_token(token.as_deref())?;
                let client = create_client(
                    &bcs_url,
                    &token,
                    bcs_cookie.as_deref(),
                    oauth_headers.as_ref(),
                );
                debug_request!(
                    debug,
                    "GET",
                    &format!("/sessions/{session}/state-machine-permission"),
                    json!({})
                );
                let permission = client
                    .get_session_state_machine_permission(&session)
                    .await?;
                debug_response!(debug, "200", &permission);
                if structured_mode {
                    println!("{}", serde_json::to_string(&permission)?);
                } else {
                    println!(
                        "State-machine permission: {}",
                        if permission
                            .get("allowed")
                            .and_then(serde_json::Value::as_bool)
                            .unwrap_or(false)
                        {
                            "ALLOWED"
                        } else {
                            "DENIED"
                        }
                    );
                    println!(
                        "  Session: {}",
                        permission
                            .get("session_id")
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or(&session)
                    );
                    println!(
                        "  Reason: {}",
                        permission
                            .get("reason_code")
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("unknown")
                    );
                    if let Some(message) =
                        permission.get("message").and_then(serde_json::Value::as_str)
                    {
                        println!("  Message: {message}");
                    }
                }
            }

            CollaborationCommands::Run {
                file,
                session,
                bindings,
                input,
                panel_component,
                panel_params,
                panel_tab_id,
                panel_tab_title,
                panel_tab_closable,
            } => {
                let definition_yaml = std::fs::read_to_string(&file).map_err(|error| {
                    anyhow!("Failed to read YAML file {}: {error}", file.display())
                })?;
                let participant_bindings = parse_custom_group_bindings(&bindings)?;
                let input = parse_json_arg(&input)?;
                let opening_message = build_panel_opening_message(
                    panel_component,
                    panel_params,
                    panel_tab_id,
                    panel_tab_title,
                    panel_tab_closable,
                )?;
                let token = get_token(token.as_deref())?;
                let client = create_client(
                    &bcs_url,
                    &token,
                    bcs_cookie.as_deref(),
                    oauth_headers.as_ref(),
                );
                let mut debug_payload = json!({
                    "definition_yaml": &definition_yaml,
                    "participant_bindings": &participant_bindings,
                    "input": &input,
                });
                if let Some(opening_message) = opening_message.as_ref() {
                    debug_payload["opening_message"] = opening_message.clone();
                }
                debug_request!(
                    debug,
                    "POST",
                    &format!("/sessions/{session}/state-machine-runs"),
                    debug_payload
                );
                let result = client
                    .run_session_collaboration(RunSessionCollaborationOptions {
                        session_id: session,
                        participant_bindings,
                        definition_yaml,
                        input,
                        opening_message,
                    })
                    .await?;
                debug_response!(debug, "202", &result);
                if structured_mode {
                    println!("{}", serde_json::to_string(&result)?);
                } else {
                    println!("State-machine run started:");
                    println!(
                        "  Run: {}",
                        result
                            .get("run")
                            .and_then(|run| run.get("run_id"))
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("?")
                    );
                    println!(
                        "  Session: {}",
                        result
                            .get("run")
                            .and_then(|run| run.get("session_id"))
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("?")
                    );
                    println!(
                        "  Status: {}",
                        result
                            .get("run")
                            .and_then(|run| run.get("status"))
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("?")
                    );
                    if result.get("node_execution_metadata").is_some() {
                        print!("{}", collaboration_output::render(&result));
                    }
                }
            }

            CollaborationCommands::Query { run, node, graph, pending } => {
                let token = get_token(token.as_deref())?;
                let client = create_client(&bcs_url, &token, bcs_cookie.as_deref(), oauth_headers.as_ref());
                let suffix = if let Some(node) = node.as_deref() { vec!["nodes", node] }
                    else if graph { vec!["graph"] } else if pending { vec!["pending-human-nodes"] } else { vec![] };
                let result = client.query_collaboration_run(&run, &suffix).await?;
                if structured_mode { println!("{}", serde_json::to_string(&result)?); }
                else { print!("{}", collaboration_output::render(&result)); }
            }

            CollaborationCommands::Respond { run, node, content } => {
                let token = get_token(token.as_deref())?;
                let client = create_client(&bcs_url, &token, bcs_cookie.as_deref(), oauth_headers.as_ref());
                let result = client.respond_collaboration_node(&run, &node, &content).await?;
                if structured_mode { println!("{}", serde_json::to_string(&result)?); }
                else { print!("{}", collaboration_output::render(&result)); }
            }

            CollaborationCommands::Validate { file } => {
                let definition_yaml = std::fs::read_to_string(&file).map_err(|error| {
                    anyhow!("Failed to read YAML file {}: {error}", file.display())
                })?;
                let token = get_token(token.as_deref())?;
                let client = create_client(
                    &bcs_url,
                    &token,
                    bcs_cookie.as_deref(),
                    oauth_headers.as_ref(),
                );

                debug_request!(
                    debug,
                    "POST",
                    "/collaboration/definitions/validate",
                    json!({ "definition_yaml": &definition_yaml })
                );
                let validation = client
                    .validate_collaboration_definition(&definition_yaml)
                    .await?;
                debug_response!(debug, "200", &validation);
                let valid = validation
                    .get("valid")
                    .and_then(serde_json::Value::as_bool)
                    .unwrap_or(false);
                emit_collaboration_validation(&validation, structured_mode);
                if !valid {
                    std::process::exit(1);
                }
            }

            CollaborationCommands::Create {
                file,
                id,
                driver,
                bindings,
                context,
                topic,
                auto_start_on_service_invocation,
                no_session,
            } => {
                let definition_yaml = std::fs::read_to_string(&file).map_err(|error| {
                    anyhow!("Failed to read YAML file {}: {error}", file.display())
                })?;
                let participant_bindings = parse_custom_group_bindings(&bindings)?;
                let token = get_token(token.as_deref())?;
                let client = create_client(
                    &bcs_url,
                    &token,
                    bcs_cookie.as_deref(),
                    oauth_headers.as_ref(),
                );

                debug_request!(
                    debug,
                    "POST",
                    "/collaboration/definitions/validate",
                    json!({ "definition_yaml": &definition_yaml })
                );
                let validation = client
                    .validate_collaboration_definition(&definition_yaml)
                    .await?;
                debug_response!(debug, "200", &validation);
                if validation
                    .get("valid")
                    .and_then(serde_json::Value::as_bool)
                    != Some(true)
                {
                    emit_collaboration_validation(&validation, structured_mode);
                    std::process::exit(1);
                }
                validate_custom_group_bindings(&participant_bindings, &validation, &driver)?;

                debug_request!(
                    debug,
                    "POST",
                    "/groups",
                    json!({
                        "id": &id,
                        "driver_bot": &driver,
                        "participant_bindings": &participant_bindings,
                        "context": &context,
                        "topic": &topic,
                        "group_strategy": "state_machine",
                        "create_initial_session": !no_session,
                        "auto_start_on_service_invocation": auto_start_on_service_invocation,
                        "collaboration_definition_yaml": &definition_yaml
                    })
                );
                let result = client
                    .create_custom_group_with_initial_session(
                        CreateCustomGroupOptions {
                            id,
                            driver_bot: driver,
                            participant_bindings,
                            definition_yaml,
                            context,
                            topic,
                            auto_start_on_service_invocation,
                        },
                        !no_session,
                    )
                    .await?;
                debug_response!(
                    debug,
                    "200",
                    json!({
                        "id": &result.id,
                        "driver_bot": &result.driver_bot,
                        "participants": &result.participants,
                        "chat_url": &result.chat_url,
                        "session_id": &result.session_id
                    })
                );

                if no_session
                    && let Some(session_id) = &result.session_id
                {
                    return Err(anyhow!(
                        "Group {} was created, but the server did not honor --no-session \
                         (created Session {}). Upgrade the server before using this option; \
                         the group and session have not been deleted.",
                        result.id, session_id
                    ));
                }
                if structured_mode {
                    println!(
                        "{}",
                        serde_json::to_string(&json!({
                            "id": result.id,
                            "driver_bot": result.driver_bot,
                            "participants": result.participants,
                            "chat_url": result.chat_url,
                            "session_id": result.session_id,
                            "group_kind": result.group_kind,
                            "created": result.created
                        }))?
                    );
                } else {
                    println!("Custom collaboration group created:");
                    println!("  ID: {}", result.id);
                    println!("  Driver: {}", result.driver_bot);
                    println!("  Participants: {}", result.participants.join(", "));
                    if let Some(chat_url) = result.chat_url {
                        println!("  Chat URL: {chat_url}");
                    }
                    if let Some(session_id) = result.session_id {
                        println!("  Session: {session_id}");
                    }
                }
            }

    }
    Ok(())
}
