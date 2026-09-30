//! Get command execution.
use super::*;

pub(super) async fn execute_get(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Get { token, bot_uuid } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", &format!("/bots/{}", &bot_uuid), json!({}));

            let bot = client.get_bot(&bot_uuid).await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "bot_id": &bot.bot_uuid,
                    "capabilities": &bot.capabilities
                })
            );

            println!("Bot: {}", bot.bot_uuid);
            if let Some(name) = &bot.capabilities.name {
                println!("  Name: {}", name);
            }
            if let Some(summary) = &bot.capabilities.summary {
                println!("  Summary: {}", summary);
            }
            if !bot.capabilities.skills.is_empty() {
                println!(
                    "  Skills: {}",
                    bot.capabilities
                        .skills
                        .iter()
                        .map(|s| s.name.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                );
            }
            if !bot.capabilities.domains.is_empty() {
                println!("  Domains: {}", bot.capabilities.domains.join(", "));
            }
            println!(
                "  Visibility: {}",
                if bot.capabilities.visibility.is_empty() {
                    "protected"
                } else {
                    &bot.capabilities.visibility
                }
            );

    Ok(())
}
