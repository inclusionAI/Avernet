//! ListGroups command execution.
use super::*;

pub(super) async fn execute_list_groups(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::ListGroups {
            token,
            batch_size,
            offset: start_offset,
            all,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, structured_mode, .. } = command_context;

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
            let next_offset = if has_more {
                Some(next_offset)
            } else {
                None
            };
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
                println!("Groups for current actor {} ({}/{}):", actor_id, returned, total);
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

    Ok(())
}
