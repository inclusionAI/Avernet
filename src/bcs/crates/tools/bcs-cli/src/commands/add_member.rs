//! AddMember command execution.
use super::*;

pub(super) async fn execute_add_member(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::AddMember {
            token,
            group,
            bot_uuid,
        } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, debug, .. } = command_context;

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
                &format!("/groups/{}/members", &group),
                json!({
                    "bot_uuid": &bot_uuid,
                })
            );

            let result = client.add_group_member(&group, &bot_uuid).await?;

            debug_response!(debug, "200", &result);

            println!("✓ Member added to group:");
            println!("  Group: {}", group);
            println!("  Bot: {}", bot_uuid);
            if let Some(role) = result
                .get("member")
                .and_then(|member| member.get("role"))
                .and_then(|role| role.as_str())
            {
                println!("  Role: {}", role);
            }

    Ok(())
}
