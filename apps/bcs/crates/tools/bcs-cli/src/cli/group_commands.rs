use std::collections::HashMap;

use anyhow::{Result, anyhow};
use serde_json::json;

use super::Commands;
use super::authentication::create_client;
use super::collaboration_input::apply_group_participant_tags;
use super::environment::get_token;
use super::output::GroupListOutput;


/// Execute a group management command: create, get, fuse, list, add
/// member, status, or terminate.
pub(super) async fn execute(
    command: Commands,
    bcs_url: String,
    bcs_cookie: Option<String>,
    oauth_headers: Option<HashMap<String, String>>,
    json: bool,
    structured_mode: bool,
    debug: bool,
) -> Result<()> {
    match command {
        Commands::CreateGroup {
            token,
            no_session,
            id: _,
            driver,
            manager,
            participants,
            participant_tags,
            context,
            topic,
        } => {
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
                        result.id,
                        session_id
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
        }

        Commands::GetGroup { token, id } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", &format!("/groups/{}", &id), json!({}));

            let group = client.get_group(&id).await?;

            debug_response!(debug, "200", &group);

            if json {
                println!("{}", serde_json::to_string(&group)?);
            } else {
                println!("Group: {}", serde_json::to_string_pretty(&group)?);
            }
        }

        Commands::Fuse {
            token,
            group,
            question,
            participants,
            focus,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let participants: Vec<String> = participants
                .split(',')
                .map(|s| s.trim().to_string())
                .collect();

            debug_request!(
                debug,
                "POST",
                &format!("/groups/{}/fuse", &group),
                json!({
                    "question": &question,
                    "participants": &participants,
                    "focus": &focus
                })
            );

            let result = client
                .fuse_context_with_focus(&group, &question, participants, focus.as_deref())
                .await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "perspectives": &result.perspectives.len(),
                    "conflicts": &result.conflicts.len(),
                    "recommendation": &result.recommendation
                })
            );

            println!("Fusion result:");
            println!("{}", serde_json::to_string_pretty(&result)?);
        }

        Commands::ListGroups {
            token,
            batch_size,
            offset: start_offset,
            all,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            if batch_size == 0 {
                return Err(anyhow!("Batch size must be greater than 0"));
            }
            let mut offset = start_offset;
            let mut groups = Vec::new();
            let mut actor_id = None;
            let (next_offset, total) = loop {
                debug_request!(
                    debug,
                    "GET",
                    "/groups/my",
                    json!({
                        "offset": offset,
                        "limit": batch_size,
                    })
                );
                let page = client.list_my_groups(offset, batch_size).await?;
                if page.offset != offset {
                    return Err(anyhow!(
                        "Invalid current actor groups pagination response: requested offset={}, received offset={}",
                        offset,
                        page.offset
                    ));
                }
                if page.actor_id.trim().is_empty() {
                    return Err(anyhow!(
                        "Current actor groups response did not identify the authenticated actor"
                    ));
                }
                if let Some(current_actor_id) = actor_id.as_deref() {
                    if current_actor_id != page.actor_id {
                        return Err(anyhow!(
                            "Current actor changed during pagination: expected {}, received {}",
                            current_actor_id,
                            page.actor_id
                        ));
                    }
                } else {
                    actor_id = Some(page.actor_id.clone());
                }
                let page_groups = page.items;
                let total = page.total;
                let page_returned = page_groups.len() as u64;
                if page_returned == 0 && offset < total {
                    return Err(anyhow!(
                        "Current actor groups pagination made no progress at offset {} while total is {}",
                        offset,
                        total
                    ));
                }
                groups.extend(page_groups);
                let next_offset = offset.saturating_add(page_returned);
                if !all || page_returned == 0 || next_offset >= total {
                    break (next_offset, total);
                }
                offset = next_offset;
            };
            let actor_id = actor_id.ok_or_else(|| {
                anyhow!("Current actor groups response did not identify the authenticated actor")
            })?;
            let returned = groups.len() as u64;
            let has_more = !all && returned > 0 && next_offset < total;
            let next_offset = if has_more { Some(next_offset) } else { None };
            let next_command = next_offset.map(|next_offset| {
                format!(
                    "bcs-cli list-groups --offset {} --batch-size {}",
                    next_offset, batch_size
                )
            });
            let output = GroupListOutput {
                items: groups,
                offset: start_offset,
                returned,
                total,
                has_more,
                next_offset,
                next_command,
            };

            debug_response!(debug, "200", &output);

            if structured_mode {
                println!("{}", serde_json::to_string(&output)?);
            } else {
                println!(
                    "Groups for current actor {} ({}/{}):",
                    actor_id, returned, total
                );
                for group in &output.items {
                    let id = group
                        .get("id")
                        .or_else(|| group.get("group_id"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown");
                    let mode = group
                        .get("mode")
                        .or_else(|| group.get("group_strategy"))
                        .or_else(|| group.get("group_kind"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown");
                    let driver = group
                        .get("driver_bot")
                        .or_else(|| group.get("coordinator_bot"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown");
                    println!("  - {} [{}] driver={}", id, mode, driver);
                }
                println!("Has more: {}", output.has_more);
                if let Some(next_command) = output.next_command.as_deref() {
                    println!("Next: {}", next_command);
                }
            }
        }

        Commands::AddMember {
            token,
            group,
            bot_uuid,
        } => {
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
                &format!("/groups/{}/members", &group),
                json!({
                    "bot_uuid": &bot_uuid,
                })
            );

            let result = client.add_group_member(&group, &bot_uuid).await?;

            debug_response!(debug, "200", &result);

            println!("✓ Member added to group:");
            println!("  Group: {}", group);
            println!("  Bot: {}", bot_uuid);
            if let Some(role) = result
                .get("member")
                .and_then(|member| member.get("role"))
                .and_then(|role| role.as_str())
            {
                println!("  Role: {}", role);
            }
        }

        Commands::GroupStatus {
            token,
            group,
            status,
            reason,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(
                debug,
                "PUT",
                &format!("/groups/{}/status", &group),
                json!({
                    "status": &status,
                    "reason": &reason
                })
            );

            let result = client
                .update_group_status(&group, &status, reason.as_deref())
                .await?;

            debug_response!(debug, "200", &result);

            if json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("✓ Group status updated:");
                println!("  Group: {}", group);
                println!("  Status: {}", status);
                if let Some(r) = reason {
                    println!("  Reason: {}", r);
                }
            }
        }

        Commands::TerminateGroup { token, group } => {
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
                &format!("/groups/{}/terminate", &group),
                json!({})
            );

            let result = client.terminate_group(&group).await?;

            debug_response!(debug, "200", &result);

            if json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("✓ Group terminated:");
                println!("  Group: {}", group);
                println!("  Status: completed");
            }
        }

        _ => unreachable!("wrong command family"),
    }

    Ok(())
}
