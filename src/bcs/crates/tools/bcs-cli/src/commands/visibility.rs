//! Visibility command execution.
use super::*;

pub(super) async fn execute_visibility(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Visibility { token, command } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, cli_json, .. } = command_context;

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
                        None => resolve_my_bot_uuid()?,
                    };

                    debug_request!(
                        debug,
                        "GET",
                        &format!("/bots/{}/visibility", &my_bot_uuid),
                        json!({})
                    );

                    let result = client.get_visibility(&my_bot_uuid).await?;

                    if cli_json {
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
                        None => resolve_my_bot_uuid()?,
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

                    if cli_json {
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

    Ok(())
}
