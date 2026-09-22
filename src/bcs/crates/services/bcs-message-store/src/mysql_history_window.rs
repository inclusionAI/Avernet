//! Resolve ordinary Chat windows using physical sequence positions only.
use super::*;

const BATCH_SIZE: usize = 512;
const MAX_SUPPLEMENTAL_ROWS: usize = 16_384;
const SUPPLEMENTAL: &str = "visibility_domain = 'state_machine' AND message_type IN ('state_machine_panel', 'state_machine_output', 'state_machine_human_input_prompt', 'state_machine_human_input_response') AND JSON_EXTRACT(CASE WHEN JSON_VALID(content) THEN content ELSE '{}' END, '$.metadata.state_machine.history_schema_version') = 1";

pub(super) fn filter(conditions: &mut Vec<String>, params: &mut Vec<DbValue>, from: i64) {
    conditions.push(format!("(session_seq >= ? OR ({SUPPLEMENTAL}))"));
    params.push(from.into());
}

/// Gaps retain their old physical-window meaning. Only retained supplemental
/// rows are skipped; their bodies/status may be redacted, but keep the marker.
pub(crate) struct HistoryWindow {
    cursor: i64,
    remaining: u64,
    supplemental: usize,
}

impl HistoryWindow {
    pub(crate) fn new(anchor: i64, limit: u64) -> Self {
        Self { cursor: anchor, remaining: limit, supplemental: 0 }
    }

    pub(crate) fn start(&self) -> i64 {
        if self.remaining == 0 { return self.cursor.saturating_add(1).max(1); }
        self.cursor.saturating_sub(i64::try_from(self.remaining - 1).unwrap_or(i64::MAX)).max(1)
    }

    pub(crate) fn consume(&mut self, seq: i64, supplemental: bool) -> Result<Option<i64>, MessageRepoError> {
        let gap = self.cursor.saturating_sub(seq).max(0) as u64;
        if gap >= self.remaining { return Ok(Some(self.start())); }
        self.remaining -= gap;
        if supplemental {
            self.supplemental += 1;
            if self.supplemental > MAX_SUPPLEMENTAL_ROWS {
                return Err(MessageRepoError::StorageError("Chat history window exceeds 16384 supplemental rows".into()));
            }
        } else {
            self.remaining -= 1;
        }
        self.cursor = seq - 1;
        Ok((self.remaining == 0 || self.cursor <= 0).then(|| self.start()))
    }
}

impl MySqlMessageStore {
    pub(super) async fn history_window_start(&self, session: &str, anchor: i64, limit: u64) -> Result<i64, MessageRepoError> {
        let mut window = HistoryWindow::new(anchor, limit);
        if anchor <= 0 || limit == 0 { return Ok(window.start()); }
        // At most limit ordinary positions + 16384 supplemental rows. Each
        // indexed batch returns only sequence and a boolean, never message bodies.
        loop {
            let rows = self.db.query(DbStatement::with_params(format!(
                "SELECT session_seq, CASE WHEN {SUPPLEMENTAL} THEN 1 ELSE 0 END AS supplemental FROM bcs_messages WHERE env = ? AND session_id = ? AND session_seq <= ? ORDER BY session_seq DESC LIMIT {BATCH_SIZE}"
            ), vec![self.env.as_str().into(), session.into(), window.cursor.into()])).await
                .map_err(|error| MessageRepoError::StorageError(error.to_string()))?;
            for row in &rows {
                let seq = db_get_column::<i64>(row, "session_seq").map_err(storage)?;
                let supplemental = db_get_column::<i64>(row, "supplemental").map_err(storage)? != 0;
                if let Some(start) = window.consume(seq, supplemental)? { return Ok(start); }
            }
            if rows.len() < BATCH_SIZE { return Ok(window.start()); }
        }
    }
}

fn storage(error: impl std::fmt::Display) -> MessageRepoError {
    MessageRepoError::StorageError(error.to_string())
}
