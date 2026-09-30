//! GroupStatus command execution.
use super::*;

pub(super) async fn execute_group_status(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::GroupStatus {
            token,
            group,
            status,
            reason,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, cli_json, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(
                debug,
                "PUT",
                &format!("/groups/{}/status", &group),
                json!({
                    "status": &status,
                    "reason": &reason
                })
            );

            let result = client
                .update_group_status(&group, &status, reason.as_deref())
                .await?;

            debug_response!(debug, "200", &result);

            if cli_json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("✓ Group status updated:");
                println!("  Group: {}", group);
                println!("  Status: {}", status);
                if let Some(r) = reason {
                    println!("  Reason: {}", r);
                }
            }

    Ok(())
}
