//! GetGroup command execution.
use super::*;

pub(super) async fn execute_get_group(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::GetGroup { token, id } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, cli_json, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", &format!("/groups/{}", &id), json!({}));

            let group = client.get_group(&id).await?;

            debug_response!(debug, "200", &group);

            if cli_json {
                println!("{}", serde_json::to_string(&group)?);
            } else {
                println!("Group: {}", serde_json::to_string_pretty(&group)?);
            }

    Ok(())
}
