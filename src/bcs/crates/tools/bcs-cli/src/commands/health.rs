//! Health command execution.
use super::*;

pub(super) async fn execute_health(command: Commands, command_context: CommandContext) -> Result<()> {
    let Commands::Health = command else { unreachable!("command dispatch mismatch") };
    let CommandContext { bcs_url, bcs_cookie, oauth_headers, structured_mode, .. } = command_context;

            let mut client = BcsClient::new(&bcs_url);
            if let Some(ref cookie) = bcs_cookie {
                client.set_cookie(cookie);
            }
            if let Some(ref headers) = oauth_headers {
                client.set_oauth_headers(headers.clone());
            }
            let healthy = match client.health_check().await {
                Ok(h) => h,
                Err(e) => {
                    // Under structured_mode, a network/connection error must
                    // surface as a structured JSON result (honoring the output
                    // contract), not a raw traceback on stderr. Human mode
                    // propagates the error unchanged.
                    if structured_mode {
                        let result = StructuredResult {
                            status: "unhealthy".to_string(),
                            message: Some(format!("BCS health check failed: {}", e)),
                            ..Default::default()
                        };
                        emit_structured_result(&result);
                        std::process::exit(1);
                    } else {
                        return Err(e);
                    }
                }
            };
            if structured_mode {
                let result = StructuredResult {
                    status: if healthy { "healthy".to_string() } else { "unhealthy".to_string() },
                    message: Some(format!("BCS is {} at {}", if healthy { "healthy" } else { "unhealthy" }, bcs_url)),
                    ..Default::default()
                };
                emit_structured_result(&result);
                if !healthy {
                    std::process::exit(1);
                }
            } else {
                if healthy {
                    println!("✓ BCS is healthy at {}", bcs_url);
                } else {
                    println!("✗ BCS health check failed");
                    std::process::exit(1);
                }
            }

    Ok(())
}
