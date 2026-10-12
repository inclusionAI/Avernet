//! Read-only correlation. Never mutates the desktop database or its schema.
use rusqlite::{Connection, OpenFlags};
use std::path::Path;

const LOOKUP: &str = "SELECT c.ext FROM sub_chats s JOIN chats c ON c.id=s.chat_id WHERE s.session_id=?1 AND c.deleted_at IS NULL LIMIT 2";

pub(crate) fn validate(database: &Path) -> Result<(), String> {
    let db = Connection::open_with_flags(database, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| e.to_string())?;
    db.prepare(LOOKUP)
        .map_err(|e| format!("unsupported QwenWork database schema: {e}"))?;
    Ok(())
}

pub(crate) fn conversation(
    database: &Path,
    session: &str,
    bot: &str,
) -> Result<Option<String>, String> {
    let db = Connection::open_with_flags(database, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("QwenWork database: {e}"))?;
    db.busy_timeout(std::time::Duration::from_millis(200))
        .map_err(|e| e.to_string())?;
    let mut query = db
        .prepare(LOOKUP)
        .map_err(|e| format!("QwenWork session lookup: {e}"))?;
    let rows = query
        .query_map([session], |r| r.get::<_, String>(0))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    if rows.len() > 1 {
        return Err("ambiguous QwenWork session mapping".into());
    }
    let Some(ext) = rows.first() else {
        return Ok(None);
    };
    let value: serde_json::Value =
        serde_json::from_str(ext).map_err(|_| "invalid QwenWork chat metadata")?;
    if value.get("channelPlatform").and_then(|v| v.as_str()) != Some("wecom-bot") {
        return Ok(None);
    }
    let prefix = format!("wecom-bot:{bot}:");
    Ok(value
        .get("imConversationId")
        .and_then(|v| v.as_str())
        .and_then(|id| id.strip_prefix(&prefix))
        .map(str::to_owned))
}
