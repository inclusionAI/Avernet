//! TerminateGroup command execution.
use super::*;

pub(super) async fn execute_terminate_group(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::TerminateGroup { token, group } = command else { unreachable!("command dispatch mismatch") };
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
                "POST",
                &format!("/groups/{}/terminate", &group),
                json!({})
            );

            let result = client.terminate_group(&group).await?;

            debug_response!(debug, "200", &result);

            if cli_json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("✓ Group terminated:");
                println!("  Group: {}", group);
                println!("  Status: completed");
            }

    Ok(())
}
