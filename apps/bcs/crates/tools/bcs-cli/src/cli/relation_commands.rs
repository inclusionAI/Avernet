use std::collections::HashMap;

use anyhow::Result;
use serde_json::json;

use super::Commands;
use super::authentication::create_client;
use super::environment::{get_token, resolve_my_bot_uuid};
use super::resource_args::{FriendCommands, VisibilityCommands};


/// Execute a relations command: friends or visibility.
pub(super) async fn execute(
    command: Commands,
    bcs_url: String,
    bcs_cookie: Option<String>,
    oauth_headers: Option<HashMap<String, String>>,
    json: bool,
    debug: bool,
) -> Result<()> {
    match command {
        Commands::Friend { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                FriendCommands::Request { bot_uuid } => {
                    debug_request!(
                        debug,
                        "POST",
                        "/friends/request",
                        json!({ "to_bot": &bot_uuid })
                    );

                    let result = client.send_friend_request(None, &bot_uuid).await?;

                    if json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({
                                "success": result.success,
                                "data": result.data,
                                "message": result.message,
                            }))?
                        );
                    } else if result.success {
                        if let Some(ref msg) = result.message {
                            println!("✓ {}", msg);
                        } else {
                            println!("✓ Friend request sent to {}", bot_uuid);
                            if let Some(ref data) = result.data {
                                if let Some(id) = data.get("id").and_then(|v| v.as_str()) {
                                    println!("  Request ID: {}", id);
                                }
                            }
                        }
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::Accept { request_id } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/friends/requests/{}/accept", &request_id),
                        json!({})
                    );

                    let result = client.accept_friend_request(&request_id).await?;

                    if json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({ "success": result.success }))?
                        );
                    } else if result.success {
                        println!("✓ Friend request accepted");
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::Reject { request_id } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/friends/requests/{}/reject", &request_id),
                        json!({})
                    );

                    let result = client.reject_friend_request(&request_id).await?;

                    if json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({ "success": result.success }))?
                        );
                    } else if result.success {
                        println!("✓ Friend request rejected");
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::List { bot_uuid } => {
                    let my_bot_uuid = match bot_uuid {
                        Some(id) => id,
                        None => resolve_my_bot_uuid(&client).await?,
                    };

                    debug_request!(
                        debug,
                        "GET",
                        &format!("/bots/{}/friends", &my_bot_uuid),
                        json!({})
                    );

                    let result = client.list_friends(&my_bot_uuid).await?;

                    if json {
                        println!("{}", serde_json::to_string(&result.data)?);
                    } else if let Some(data) = result.data {
                        if let Some(friends) = data.as_array() {
                            println!("Friends ({}):", friends.len());
                            for friend in friends {
                                let uuid = friend
                                    .get("bot_uuid")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("?");
                                let name = friend
                                    .get("name")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("unnamed");
                                let online = friend
                                    .get("is_online")
                                    .and_then(|v| v.as_bool())
                                    .unwrap_or(false);
                                let status_icon = if online { "🟢" } else { "⚪" };
                                println!("  {} {} ({})", status_icon, name, uuid);
                            }
                        } else {
                            println!("No friends found.");
                        }
                    }
                }

                FriendCommands::Requests { direction, status } => {
                    debug_request!(
                        debug,
                        "GET",
                        "/friends/requests",
                        json!({
                            "direction": &direction,
                            "status": &status
                        })
                    );

                    let result = client
                        .list_friend_requests(None, Some(&direction), status.as_deref())
                        .await?;

                    if json {
                        println!("{}", serde_json::to_string(&result.data)?);
                    } else if let Some(data) = result.data {
                        if let Some(requests) = data.as_array() {
                            println!("Friend requests ({}):", requests.len());
                            for req in requests {
                                let id = req.get("id").and_then(|v| v.as_str()).unwrap_or("?");
                                let from =
                                    req.get("from_bot").and_then(|v| v.as_str()).unwrap_or("?");
                                let to = req.get("to_bot").and_then(|v| v.as_str()).unwrap_or("?");
                                let req_status =
                                    req.get("status").and_then(|v| v.as_str()).unwrap_or("?");
                                println!("  {} → {} [{}] (id: {})", from, to, req_status, id);
                            }
                        } else {
                            println!("No friend requests found.");
                        }
                    }
                }
            }
        }

        Commands::Visibility { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                VisibilityCommands::Get { bot_uuid } => {
                    let my_bot_uuid = match bot_uuid {
                        Some(id) => id,
                        None => resolve_my_bot_uuid(&client).await?,
                    };

                    debug_request!(
                        debug,
                        "GET",
                        &format!("/bots/{}/visibility", &my_bot_uuid),
                        json!({})
                    );

                    let result = client.get_visibility(&my_bot_uuid).await?;

                    if json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({
                                "success": result.success,
                                "data": result.data,
                            }))?
                        );
                    } else if let Some(data) = result.data {
                        let vis = data
                            .get("visibility")
                            .and_then(|v| v.as_str())
                            .unwrap_or("unknown");
                        println!("Visibility: {}", vis);
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                VisibilityCommands::Set { value, bot_uuid } => {
                    let my_bot_uuid = match bot_uuid {
                        Some(id) => id,
                        None => resolve_my_bot_uuid(&client).await?,
                    };

                    debug_request!(
                        debug,
                        "PUT",
                        &format!("/bots/{}/visibility", &my_bot_uuid),
                        json!({
                            "visibility": &value
                        })
                    );

                    let result = client.set_visibility(&my_bot_uuid, &value).await?;

                    if json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({
                                "success": result.success,
                                "data": result.data,
                            }))?
                        );
                    } else if result.success {
                        println!("✓ Visibility set to '{}'", value);
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }
            }
        }

        _ => unreachable!("wrong command family"),
    }

    Ok(())
}
