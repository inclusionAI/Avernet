//! RequestGroupHelp command execution.
use super::*;

pub(super) async fn execute_request_group_help(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::RequestGroupHelp {
            token,
            topic,
            participants,
            driver,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, cli_json, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let suggested_participants: Option<Vec<String>> =
                participants.map(|p| p.split(',').map(|s| s.trim().to_string()).collect());

            skill_debug_request!(
                debug,
                "POST",
                "/groups/request",
                json!({
                    "topic": &topic,
                    "suggested_participants": &suggested_participants,
                    "driver": &driver
                })
            );

            let result = client
                .propose_group_chat_with_token(&topic, suggested_participants, driver.as_deref())
                .await?;

            skill_debug_response!(
                debug,
                "200",
                json!({
                    "mode": &result.mode,
                    "driver_bot": &result.driver_bot,
                    "participants": &result.participants,
                    "confirm_url": &result.confirm_url
                })
            );

            if cli_json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("Proposal created:");
                println!("  Mode: {}", result.mode);
                println!("  Driver: {}", result.driver_bot);
                println!("  Participants: {}", result.participants.join(", "));
                println!("  Confirm URL: {}", result.confirm_url);
                println!("  Message: {}", result.message);
            }

    Ok(())
}
