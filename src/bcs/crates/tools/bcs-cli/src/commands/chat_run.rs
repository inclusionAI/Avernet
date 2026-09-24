//! ChatRun command execution.
use super::*;

pub(super) async fn execute_chat_run(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::ChatRun { token, command } = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, structured_mode, .. } = command_context;

            let token = get_token(token.as_deref())?;
            let client = create_client(&bcs_url, &token, bcs_cookie.as_deref(), oauth_headers.as_ref());
            let result = match command {
                ChatRunCommands::Status { run_id } => serde_json::to_value(client.chat_run_status(&run_id, None, None).await?)?,
                ChatRunCommands::Cancel { run_id } => serde_json::to_value(client.chat_run_cancel(&run_id).await?)?,
            };
            if structured_mode { println!("{}", serde_json::to_string(&result)?); }
            else {
                println!("Run: {}", result["run_id"].as_str().unwrap_or("unknown"));
                println!("State: {}", result["state"].as_str().unwrap_or("unknown"));
                if let Some(status) = result.get("delivery").and_then(|d| d.get("status")).and_then(|v| v.as_str()) {
                    println!("Delivery: {}", status);
                    if matches!(status, "cancelling" | "cancel_unknown") { println!("Cancellation is not confirmed; query status again."); }
                }
            }

    Ok(())
}
