//! List command execution.
use super::*;

pub(super) async fn execute_list(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::List { token } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", "/bots", json!({}));

            let bots = client.list_bots().await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "count": bots.len()
                })
            );

            println!("Bots in network ({}):", bots.len());
            for bot in bots {
                println!(
                    "  - {} ({})",
                    bot.bot_uuid,
                    bot.capabilities.name.as_deref().unwrap_or("unnamed")
                );
                if let Some(summary) = &bot.capabilities.summary {
                    println!("    {}", summary);
                }
            }

    Ok(())
}
