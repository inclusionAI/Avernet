//! Discover command execution.
use super::*;

pub(super) async fn execute_discover(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Discover {
            token,
            query,
            skills,
            visibility,
            collaborate_bot,
            organization_code,
            role,
            no_json,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, structured_mode, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(
                debug,
                "GET",
                "/bots/discover",
                json!({
                    "q": &query,
                    "skills": &skills,
                    "visibility": &visibility,
                    "collaborate_bot": &collaborate_bot,
                    "organization_code": &organization_code,
                    "role": &role,
                })
            );

            let result = client
                .discover_bots_extended(
                    query.as_deref(),
                    &skills,
                    visibility.as_deref(),
                    collaborate_bot.as_deref(),
                    organization_code.as_deref(),
                    role.as_deref(),
                )
                .await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "count": result.count
                })
            );

            println!(
                "{}",
                render_discover_result(&result, structured_mode && !no_json)?
            );

    Ok(())
}
