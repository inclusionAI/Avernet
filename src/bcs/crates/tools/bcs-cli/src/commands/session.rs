//! Session command execution.
use super::*;

pub(super) async fn execute_session(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Session { token, command } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, cli_json, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                SessionCommands::Create {
                    group,
                    title,
                    kind,
                    input,
                    meta,
                    group_context_delivery,
                } => {
                    let input_json = input
                        .as_deref()
                        .map(serde_json::from_str::<serde_json::Value>)
                        .transpose()
                        .map_err(|e| anyhow!("Invalid --input JSON: {}", e))?;
                    let meta_json = meta
                        .as_deref()
                        .map(serde_json::from_str::<serde_json::Value>)
                        .transpose()
                        .map_err(|e| anyhow!("Invalid --meta JSON: {}", e))?;

                    debug_request!(
                        debug,
                        "POST",
                        &format!("/groups/{}/sessions", &group),
                        json!({
                            "session_title": &title,
                            "session_kind": &kind,
                            "input": &input_json,
                            "meta": &meta_json,
                            "group_context_delivery": &group_context_delivery,
                        })
                    );

                    let result = client
                        .create_session(
                            &group,
                            title.as_deref(),
                            kind.as_deref(),
                            input_json.as_ref(),
                            meta_json.as_ref(),
                            group_context_delivery.as_deref(),
                        )
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let sid = result
                            .get("session_id")
                            .or_else(|| result.get("id"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("?");
                        let kind = result
                            .get("session_kind")
                            .and_then(|v| v.as_str())
                            .unwrap_or("chat");
                        let status = result
                            .get("status")
                            .and_then(|v| v.as_str())
                            .unwrap_or("?");
                        println!("✓ Session created: {} (kind={}, status={})", sid, kind, status);
                    }
                }

                SessionCommands::List {
                    group,
                    status,
                    q,
                    participant,
                    offset,
                    limit,
                } => {
                    debug_request!(
                        debug,
                        "GET",
                        &format!("/groups/{}/sessions", &group),
                        json!({
                            "status": &status,
                            "q": &q,
                            "participant": &participant,
                            "offset": &offset,
                            "limit": &limit,
                        })
                    );

                    let result = client
                        .list_sessions(
                            &group,
                            status.as_deref(),
                            q.as_deref(),
                            participant.as_deref(),
                            offset,
                            limit,
                        )
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let items = result
                            .get("items")
                            .and_then(|v| v.as_array())
                            .cloned()
                            .unwrap_or_default();
                        println!("Sessions in group {} ({}):", group, items.len());
                        for item in items {
                            let sid = item
                                .get("session_id")
                                .or_else(|| item.get("id"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let st = item
                                .get("status")
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let title = item
                                .get("session_title")
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            println!("  - {} [{}] {}", sid, st, title);
                        }
                    }
                }

                SessionCommands::Get { session } => {
                    debug_request!(debug, "GET", &format!("/sessions/{}", &session), json!({}));
                    let result = client.get_session(&session).await?;
                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("{}", serde_json::to_string_pretty(&result)?);
                    }
                }

                SessionCommands::Chat { session, message } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/sessions/{}/chat", &session),
                        json!({ "message": &message })
                    );

                    let result = client.session_chat(&session, &message).await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let delivered = result
                            .get("delivered_count")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0);
                        let failed = result
                            .get("failed_count")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0);
                        println!("✓ Delivered to {} (failed {})", delivered, failed);
                        if let Some(mentions) =
                            result.get("mentions").and_then(|v| v.as_array())
                        {
                            if !mentions.is_empty() {
                                let names: Vec<String> = mentions
                                    .iter()
                                    .filter_map(|m| m.as_str().map(|s| s.to_string()))
                                    .collect();
                                if !names.is_empty() {
                                    println!("  @mentions: {}", names.join(", "));
                                }
                            }
                        }
                    }
                }

                SessionCommands::Messages {
                    session,
                    view_bot,
                    limit,
                    before,
                } => {
                    debug_request!(
                        debug,
                        "GET",
                        &format!("/sessions/{}/messages", &session),
                        json!({
                            "view_bot_id": &view_bot,
                            "limit": &limit,
                            "before": &before,
                        })
                    );

                    let result = client
                        .session_messages(&session, view_bot.as_deref(), limit, before)
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let messages = result.as_array().cloned().unwrap_or_default();
                        println!("Messages in session {} ({}):", session, messages.len());
                        for msg in messages {
                            let ts = msg
                                .get("ts")
                                .or_else(|| msg.get("timestamp"))
                                .and_then(|v| v.as_u64())
                                .unwrap_or(0);
                            let sender = msg
                                .get("sender")
                                .or_else(|| msg.get("from"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let content = msg
                                .get("content")
                                .or_else(|| msg.get("message"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            // UTF-8 safe truncation per src/bcs/CLAUDE.md
                            let preview: &str = match content.char_indices().nth(80) {
                                Some((idx, _)) => &content[..idx],
                                None => content,
                            };
                            println!("  [{}] {}: {}", ts, sender, preview);
                        }
                    }
                }

                SessionCommands::Patch { session, title } => {
                    debug_request!(
                        debug,
                        "PATCH",
                        &format!("/sessions/{}", &session),
                        json!({ "session_title": &title })
                    );

                    let result = client.patch_session(&session, &title).await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let sid = result
                            .get("session_id")
                            .or_else(|| result.get("id"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("?");
                        let new_title = result
                            .get("session_title")
                            .and_then(|v| v.as_str())
                            .unwrap_or("?");
                        println!("✓ Patched: {} title=\"{}\"", sid, new_title);
                    }
                }

                SessionCommands::Complete {
                    session,
                    output,
                    error,
                } => {
                    let output_json = output
                        .as_deref()
                        .map(parse_json_arg)
                        .transpose()
                        .map_err(|e| anyhow!("--output: {}", e))?;

                    debug_request!(
                        debug,
                        "POST",
                        &format!("/sessions/{}/complete", &session),
                        json!({
                            "output": &output_json,
                            "error": &error,
                        })
                    );

                    let result = client
                        .complete_session(&session, output_json.as_ref(), error.as_deref())
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else if result.get("already_completed").and_then(|v| v.as_bool()) == Some(true)
                    {
                        println!("↺ Already completed: {}", session);
                    } else {
                        let status = result
                            .get("status")
                            .and_then(|v| v.as_str())
                            .unwrap_or("completed");
                        println!("✓ Completed: {} status={}", session, status);
                    }
                }

                SessionCommands::AddMember {
                    session,
                    bot_uuid,
                    role,
                } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/sessions/{}/members", &session),
                        json!({
                            "bot_uuid": &bot_uuid,
                            "role": &role,
                        })
                    );

                    let result = client
                        .add_session_member(&session, &bot_uuid, role.as_deref())
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let effective_role = result
                            .get("participants")
                            .and_then(|v| v.as_array())
                            .and_then(|arr| {
                                arr.iter()
                                    .find(|p| p.get("bot_uuid").and_then(|v| v.as_str()) == Some(&bot_uuid))
                            })
                            .and_then(|p| p.get("role").and_then(|v| v.as_str()))
                            .unwrap_or("?");
                        println!(
                            "✓ Added member {} to {} (role={})",
                            bot_uuid, session, effective_role
                        );
                    }
                }

                SessionCommands::RemoveMember {
                    session,
                    bot_uuid,
                } => {
                    debug_request!(
                        debug,
                        "DELETE",
                        &format!("/sessions/{}/members/{}", &session, &bot_uuid),
                        json!({})
                    );

                    let result = client
                        .remove_session_member(&session, &bot_uuid)
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Removed member {} from {}", bot_uuid, session);
                    }
                }

                SessionCommands::SetMemberMode {
                    session,
                    bot_uuid,
                    mode,
                } => {
                    debug_request!(
                        debug,
                        "PATCH",
                        &format!("/sessions/{}/members/{}", &session, &bot_uuid),
                        json!({ "mode": &mode })
                    );

                    let result = client
                        .set_session_member_mode(&session, &bot_uuid, &mode)
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Mode set {}@{} -> {}", bot_uuid, session, mode);
                    }
                }

                SessionCommands::InviteLink {
                    session,
                    ttl_seconds,
                } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/sessions/{}/invite-link", &session),
                        json!({ "ttl_seconds": ttl_seconds })
                    );

                    let result = client
                        .create_session_invite_link(&session, ttl_seconds)
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli_json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        if let Some(link) = result.get("link").and_then(|v| v.as_str()) {
                            println!("✓ Invite link created:");
                            println!("  {}", link);
                            if let Some(expires_at) = result.get("expires_at").and_then(|v| v.as_u64()) {
                                println!("  Expires: {} (Unix ms)", expires_at);
                            }
                        } else {
                            println!("{}", serde_json::to_string_pretty(&result)?);
                        }
                    }
                }

                SessionCommands::File { command } => {
                    match command {
                        SessionFileCommands::Upload { session, path, name, mime } => {
                            debug_request!(
                                debug,
                                "POST",
                                &format!("/sessions/{}/files", &session),
                                json!({ "path": &path, "name": &name, "mime": &mime })
                            );

                            let result = client
                                .upload_session_file(&session, &path, name.as_deref(), mime.as_deref())
                                .await?;

                            debug_response!(debug, "200", &result);

                            if cli_json {
                                println!("{}", serde_json::to_string(&result)?);
                            } else {
                                let fid = result
                                    .get("file_id")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("?");
                                let size = result
                                    .get("size")
                                    .and_then(|v| v.as_u64())
                                    .unwrap_or(0);
                                println!("✓ Uploaded: {} ({})", fid, size);
                            }
                        }
                        SessionFileCommands::List { session, prefix, status, limit, offset } => {
                            debug_request!(
                                debug,
                                "GET",
                                &format!("/sessions/{}/files", &session),
                                json!({ "prefix": &prefix, "status": &status, "limit": &limit, "offset": &offset })
                            );

                            let result = client
                                .list_session_files(&session, prefix.as_deref(), status.as_deref(), limit, offset)
                                .await?;

                            debug_response!(debug, "200", &result);

                            if cli_json {
                                println!("{}", serde_json::to_string(&result)?);
                            } else {
                                let items = result
                                    .get("items")
                                    .or_else(|| result.get("files"))
                                    .and_then(|v| v.as_array())
                                    .cloned()
                                    .unwrap_or_default();
                                let total = result
                                    .get("total")
                                    .and_then(|v| v.as_u64())
                                    .unwrap_or(items.len() as u64);
                                println!(
                                    "Files in session {} ({} of {}):",
                                    session,
                                    items.len(),
                                    total
                                );
                                for item in items {
                                    let id = item
                                        .get("file_id")
                                        .or_else(|| item.get("id"))
                                        .and_then(|v| v.as_str())
                                        .unwrap_or("?");
                                    let name = item
                                        .get("file_name")
                                        .or_else(|| item.get("name"))
                                        .and_then(|v| v.as_str())
                                        .unwrap_or("");
                                    let size = item
                                        .get("size")
                                        .and_then(|v| v.as_u64())
                                        .unwrap_or(0);
                                    let status = item
                                        .get("status")
                                        .and_then(|v| v.as_str())
                                        .unwrap_or("?");
                                    println!("  - {} \"{}\" ({} bytes) [{}]", id, name, size, status);
                                }
                            }
                        }
                        SessionFileCommands::Download { session, file_id, out, ttl } => {
                            debug_request!(
                                debug,
                                "GET",
                                &format!("/sessions/{}/files/{}/content", &session, &file_id),
                                json!({ "out": &out, "ttl": &ttl })
                            );

                            let out_path = client
                                .download_session_file(&session, &file_id, out.as_deref(), ttl)
                                .await?;

                            debug_response!(debug, "200", json!({ "out": &out_path }));

                            if cli_json {
                                println!("{}", json!({ "out": out_path }));
                            } else {
                                println!("✓ Downloaded to {}", out_path);
                            }
                        }
                        SessionFileCommands::Delete { session, file_id } => {
                            debug_request!(
                                debug,
                                "DELETE",
                                &format!("/sessions/{}/files/{}", &session, &file_id),
                                json!({})
                            );

                            let result = client
                                .delete_session_file(&session, &file_id)
                                .await?;

                            debug_response!(debug, "200", &result);

                            if cli_json {
                                println!("{}", serde_json::to_string(&result)?);
                            } else {
                                println!("✓ Deleted: {}", file_id);
                            }
                        }
                        SessionFileCommands::Share { session, file_id, ttl } => {
                            debug_request!(
                                debug,
                                "POST",
                                &format!("/sessions/{}/files/{}/share", &session, &file_id),
                                json!({ "ttl": &ttl })
                            );

                            let result = client
                                .share_session_file(&session, &file_id, ttl)
                                .await?;

                            debug_response!(debug, "200", &result);

                            if cli_json {
                                println!("{}", serde_json::to_string(&result)?);
                            } else {
                                if let Some(link) = result.get("share_url").and_then(|v| v.as_str()) {
                                    println!("✓ Share link:");
                                    println!("  {}", link);
                                    if let Some(expires) = result.get("expires_at").and_then(|v| v.as_u64()) {
                                        println!("  Expires: {} (Unix ms)", expires);
                                    }
                                } else {
                                    println!("{}", serde_json::to_string_pretty(&result)?);
                                }
                            }
                        }
                        SessionFileCommands::Capabilities { session } => {
                            debug_request!(
                                debug,
                                "GET",
                                &format!("/sessions/{}/files/capabilities", &session),
                                json!({})
                            );

                            let result = client
                                .session_file_capabilities(&session)
                                .await?;

                            debug_response!(debug, "200", &result);

                            if cli_json {
                                println!("{}", serde_json::to_string(&result)?);
                            } else {
                                println!("{}", serde_json::to_string_pretty(&result)?);
                            }
                        }
                    }
                }
            }

    Ok(())
}
