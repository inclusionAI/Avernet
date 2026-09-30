//! Fuse command execution.
use super::*;

pub(super) async fn execute_fuse(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Fuse {
            token,
            group,
            question,
            participants,
            focus,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let participants: Vec<String> = participants
                .split(',')
                .map(|s| s.trim().to_string())
                .collect();

            debug_request!(
                debug,
                "POST",
                &format!("/groups/{}/fuse", &group),
                json!({
                    "question": &question,
                    "participants": &participants,
                    "focus": &focus
                })
            );

            let result = client
                .fuse_context_with_focus(&group, &question, participants, focus.as_deref())
                .await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "perspectives": &result.perspectives.len(),
                    "conflicts": &result.conflicts.len(),
                    "recommendation": &result.recommendation
                })
            );

            println!("Fusion result:");
            println!("{}", serde_json::to_string_pretty(&result)?);

    Ok(())
}
