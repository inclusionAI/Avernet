use anyhow::Result;
use bcs_cli::BcsClient;
use serde_json::json;

use super::session_args::SessionFileCommands;

/// Execute a session file command: upload, list, download, delete, share,
/// or capabilities.
pub(super) async fn execute(command: SessionFileCommands, client: &BcsClient, json: bool, debug: bool) -> Result<()> {
    match command {
                    SessionFileCommands::Upload {
                        session,
                        path,
                        name,
                        mime,
                    } => {
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

                        if json {
                            println!("{}", serde_json::to_string(&result)?);
                        } else {
                            let fid = result
                                .get("file_id")
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let size = result.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
                            println!("✓ Uploaded: {} ({})", fid, size);
                        }
                    }
                    SessionFileCommands::List {
                        session,
                        prefix,
                        status,
                        limit,
                        offset,
                    } => {
                        debug_request!(
                            debug,
                            "GET",
                            &format!("/sessions/{}/files", &session),
                            json!({ "prefix": &prefix, "status": &status, "limit": &limit, "offset": &offset })
                        );

                        let result = client
                            .list_session_files(
                                &session,
                                prefix.as_deref(),
                                status.as_deref(),
                                limit,
                                offset,
                            )
                            .await?;

                        debug_response!(debug, "200", &result);

                        if json {
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
                                let size = item.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
                                let status =
                                    item.get("status").and_then(|v| v.as_str()).unwrap_or("?");
                                println!("  - {} \"{}\" ({} bytes) [{}]", id, name, size, status);
                            }
                        }
                    }
                    SessionFileCommands::Download {
                        session,
                        file_id,
                        out,
                        ttl,
                    } => {
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

                        if json {
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

                        let result = client.delete_session_file(&session, &file_id).await?;

                        debug_response!(debug, "200", &result);

                        if json {
                            println!("{}", serde_json::to_string(&result)?);
                        } else {
                            println!("✓ Deleted: {}", file_id);
                        }
                    }
                    SessionFileCommands::Share {
                        session,
                        file_id,
                        ttl,
                    } => {
                        debug_request!(
                            debug,
                            "POST",
                            &format!("/sessions/{}/files/{}/share", &session, &file_id),
                            json!({ "ttl": &ttl })
                        );

                        let result = client.share_session_file(&session, &file_id, ttl).await?;

                        debug_response!(debug, "200", &result);

                        if json {
                            println!("{}", serde_json::to_string(&result)?);
                        } else {
                            if let Some(link) = result.get("share_url").and_then(|v| v.as_str()) {
                                println!("✓ Share link:");
                                println!("  {}", link);
                                if let Some(expires) =
                                    result.get("expires_at").and_then(|v| v.as_u64())
                                {
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

                        let result = client.session_file_capabilities(&session).await?;

                        debug_response!(debug, "200", &result);

                        if json {
                            println!("{}", serde_json::to_string(&result)?);
                        } else {
                            println!("{}", serde_json::to_string_pretty(&result)?);
                        }
                    }
    }

    Ok(())
}
