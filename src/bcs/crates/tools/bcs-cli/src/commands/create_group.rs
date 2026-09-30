//! CreateGroup command execution.
use super::*;

pub(super) async fn execute_create_group(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::CreateGroup {
            token,
            no_session,
            id: _,
            driver,
            manager,
            participants,
            participant_tags,
            context,
            topic,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let (driver, group_strategy) = match (driver, manager) {
                (Some(driver), None) => (driver, None),
                (None, Some(manager)) => (manager, Some("manager_worker")),
                _ => return Err(anyhow!("Exactly one of --driver or --manager is required")),
            };

            // Format: bot_uuid (may contain colons, e.g. "20260412_347nf7bz:100005")
            // Comma-separated for multiple participants. In manager-worker groups,
            // the manager role is derived from --manager and every other participant
            // is a worker, so role suffixes do not conflict with bot UUID syntax.
            let mut participants: Vec<bcs_protocol::ParticipantInfo> = participants
                .split(',')
                .map(|p| bcs_protocol::ParticipantInfo {
                    bot_uuid: p.trim().to_string(),
                    role: group_strategy.map(|_| {
                        if p.trim() == driver {
                            "manager".to_string()
                        } else {
                            "worker".to_string()
                        }
                    }),
                    tags: Vec::new(),
                    message_view_scope: None,
                })
                .collect();
            if group_strategy.is_some()
                && !participants
                    .iter()
                    .any(|participant| participant.bot_uuid == driver)
            {
                participants.insert(
                    0,
                    bcs_protocol::ParticipantInfo {
                        bot_uuid: driver.clone(),
                        role: Some("manager".to_string()),
                        tags: Vec::new(),
                        message_view_scope: None,
                    },
                );
            }
            apply_group_participant_tags(
                &mut participants,
                &driver,
                group_strategy.map(|_| "manager"),
                &participant_tags,
            )?;

            debug_request!(
                debug,
                "POST",
                "/groups",
                json!({
                    "driver_bot": &driver,
                    "participants": &participants,
                    "group_strategy": group_strategy,
                    "create_initial_session": !no_session
                })
            );

            let result = client
                .create_group_with_initial_session(
                    &driver,
                    participants,
                    context.as_deref(),
                    topic.as_deref(),
                    group_strategy,
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

            // Surface the session the server auto-creates as part of group
            // creation. New servers return it directly; retain a best-effort
            // lookup for compatibility with older servers.
            let auto_session_id = if no_session {
                if let Some(session_id) = &result.session_id {
                    return Err(anyhow!(
                        "Group {} was created, but the server did not honor --no-session \
                         (created Session {}). Upgrade the server before using this option; \
                         the group and session have not been deleted.",
                        result.id, session_id
                    ));
                }
                None
            } else if result.session_id.is_some() {
                result.session_id.clone()
            } else {
                debug_request!(
                    debug,
                    "GET",
                    &format!("/groups/{}/sessions?limit=1", &result.id),
                    json!({})
                );
                match client
                    .list_sessions(&result.id, None, None, None, None, Some(1))
                    .await
                {
                    Ok(v) => {
                        debug_response!(debug, "200", &v);
                        v.get("items")
                            .and_then(|x| x.as_array())
                            .and_then(|arr| arr.first())
                            .and_then(|session| {
                                session
                                    .get("session_id")
                                    .or_else(|| session.get("id"))
                                    .and_then(|value| value.as_str())
                            })
                            .map(str::to_string)
                    }
                    Err(e) => {
                        eprintln!(
                            "Warning: group {} created but session lookup failed: {}",
                            result.id, e
                        );
                        None
                    }
                }
            };

            println!("Group created:");
            println!("  ID: {}", result.id);
            println!("  Driver: {}", result.driver_bot);
            println!("  Participants: {}", result.participants.join(", "));
            if let Some(chat_url) = &result.chat_url {
                println!("  Chat URL: {}", chat_url);
            }
            if let Some(session_id) = auto_session_id {
                println!("  Session: {}", session_id);
            }

    Ok(())
}
