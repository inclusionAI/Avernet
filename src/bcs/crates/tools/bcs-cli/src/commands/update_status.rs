//! UpdateStatus command execution.
use super::*;

pub(super) async fn execute_update_status(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::UpdateStatus {
            token,
            status,
            summary,
            load,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let dynamic_status = bcs_protocol::BotDynamicStatus {
                status,
                dynamic_summary: summary,
                load,
                updated_at: Some(
                    std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)?
                        .as_millis() as u64,
                ),
            };

            debug_request!(
                debug,
                "POST",
                "/bots/status",
                json!({
                    "status": &dynamic_status
                })
            );

            let result = client.update_status_with_token(dynamic_status).await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "updated": result.updated
                })
            );

            if result.updated {
                println!("✓ Status updated");
            } else {
                println!("✗ Failed to update status");
            }

    Ok(())
}
