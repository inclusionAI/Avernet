//! Restricted local MCP client: only inspect and answer the correlated task.
use bcs_bridge_core::engine::TurnError;
use serde_json::{json, Value};
use std::{path::Path, time::Duration};

#[cfg(unix)]
async fn call(socket: &Path, tool: &str, args: Value) -> Result<Value, TurnError> {
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt};
    let operation = async {
        let mut stream = tokio::net::UnixStream::connect(socket).await.map_err(TurnError::Io)?;
        let request = json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":tool,"arguments":args}});
        stream.write_all(format!("{request}\n").as_bytes()).await.map_err(TurnError::Io)?;
        let mut reader = tokio::io::BufReader::new(stream);
        let mut line = Vec::new();
        loop {
            let bytes = reader.fill_buf().await.map_err(TurnError::Io)?;
            if bytes.is_empty() { return Err(TurnError::Protocol("Desktop MCP closed without response".into())); }
            let end = bytes.iter().position(|b| *b == b'\n');
            let count = end.map(|p| p + 1).unwrap_or(bytes.len());
            if line.len() + count > crate::MAX_BODY { return Err(TurnError::Protocol("Desktop MCP response too large".into())); }
            line.extend_from_slice(&bytes[..count]);
            reader.consume(count);
            if end.is_some() { break; }
        }
        let value: Value = serde_json::from_slice(&line).map_err(|e| TurnError::Protocol(e.to_string()))?;
        if value["id"] != 1 || value.get("error").is_some() {
            return Err(TurnError::Protocol("Desktop MCP rejected question operation".into()));
        }
        let text = value["result"]["content"][0]["text"].as_str().ok_or_else(|| TurnError::Protocol("Desktop MCP result missing".into()))?;
        let data: Value = serde_json::from_str(text).map_err(|e| TurnError::Protocol(e.to_string()))?;
        Ok(data)
    };
    tokio::time::timeout(Duration::from_secs(5), operation).await
        .map_err(|_| TurnError::Protocol("Desktop MCP timed out".into()))?
}

#[cfg(not(unix))]
async fn call(_socket: &Path, _tool: &str, _args: Value) -> Result<Value, TurnError> {
    Err(TurnError::Protocol("QwenWork question control requires a Unix socket".into()))
}

pub(crate) async fn answer(socket: &Path, chat: &str, tool_id: &str, answers: &Value) -> Result<(), TurnError> {
    let wait = async {
        // The desktop implementation loads the whole task history even with limit=1.
        // Inspect once; never poll that growing history while waiting for permission.
        let data = call(socket, "qwenwork_task_get_detail", json!({"chatId":chat,"limit":1})).await?;
        if data["success"] != true || data["status"] != "running" {
            return Err(TurnError::Protocol("Question task is no longer running".into()));
        }
        let tools = data["messages"].as_array().and_then(|m| m.first())
            .and_then(|m| m["toolCalls"].as_array());
        let tool = tools.and_then(|t| t.iter().find(|t| t["toolCallId"] == tool_id && t["toolName"] == "AskUserQuestion"))
            .ok_or_else(|| TurnError::Protocol("Native question does not match this tool call".into()))?;
        if tool["state"] != "call" {
            return Err(TurnError::Protocol("Question already resolved in the desktop app".into()));
        }
        for attempt in 0..10 {
            // Only a definite no-pending rejection permits another attempt.
            // Native answer attempts read task/subchat metadata and in-memory
            // approvals, never chat history. Later AskUserQuestion starts are gated.
            let result = call(socket, "qwenwork_task_submit_response", json!({"chatId":chat,"action":"answer","answers":answers})).await?;
            if result["success"] == true && result["respondedTo"] == "ask_user_question" {
                return Ok(());
            }
            if result["error"] != "No pending AskUserQuestion found for this task." {
                return Err(TurnError::Protocol("Desktop did not confirm an AskUserQuestion answer".into()));
            }
            tokio::time::sleep(Duration::from_millis((200u64 << attempt.min(3)).min(1000))).await;
        }
        Err(TurnError::Protocol("Native question did not become pending".into()))
    };
    tokio::time::timeout(Duration::from_secs(10), wait).await
        .map_err(|_| TurnError::Protocol("Native question answer timed out".into()))?
}
