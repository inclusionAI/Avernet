//! ConfirmGroupHelp command execution.
use super::*;

pub(super) async fn execute_confirm_group_help(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::ConfirmGroupHelp { url } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, debug, cli_json, .. } = command_context;

            // Confirm URL contains its own token, so we don't need the auth token
            let mut client = BcsClient::new(&bcs_url);
            if let Some(ref cookie) = bcs_cookie {
                client.set_cookie(cookie);
            }

            skill_debug_request!(debug, "POST", &url, json!({}));

            let result = client.confirm_proposal(&url).await?;

            skill_debug_response!(
                debug,
                "200",
                json!({
                    "group_id": &result.group_id,
                    "mode": &result.mode,
                    "driver_bot": &result.driver_bot,
                    "participants": &result.participants,
                    "chat_url": &result.chat_url,
                    "session_id": &result.session_id
                })
            );

            if cli_json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("Group created:");
                println!("  ID: {}", result.group_id);
                if let Some(mode) = &result.mode {
                    println!("  Mode: {}", mode);
                }
                println!("  Driver: {}", result.driver_bot);
                println!("  Participants: {}", result.participants.join(", "));
                if let Some(ref chat_url) = result.chat_url {
                    println!("  Chat URL: {}", chat_url);
                }
                if let Some(ref session_id) = result.session_id {
                    println!("  Session: {}", session_id);
                }
            }

    Ok(())
}
