//! Scheduler scans must advance past rows that cannot be decoded.
//!
//! The scan pages are ordered by deadline or id without a cursor, so a row
//! that is skipped after decoding would keep occupying its page slot and hide
//! the healthy rows behind it. Undecodable ids are remembered per process and
//! excluded from later scans. Nothing is written back: a decode failure can
//! also be version skew during a rolling deploy, which must not destroy data.
use super::{row_to_delivery, storage};
use bcs_db_api::{DbPlugin, DbRow, DbStatement, DbValue};
use bcs_domain::message_delivery::PersistedMessageDelivery;
use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError;
use std::collections::BTreeSet;

/// Bounds the exclusion clause; beyond this the remaining corrupt rows are
/// only skipped (logged by `row_to_delivery`) and need manual cleanup.
const MAX_EXCLUDED: usize = 256;

#[derive(Default)]
pub(crate) struct CorruptRows(std::sync::Mutex<BTreeSet<String>>);

impl CorruptRows {
    /// Corrupt ids plus the caller's `extra` exclusions (scheduler-isolated
    /// rows), both bounded, as one `NOT IN` clause.
    fn exclusion(&self, extra: &[String]) -> (String, Vec<DbValue>) {
        let ids = self.0.lock().unwrap_or_else(|e| e.into_inner());
        let all: BTreeSet<&str> = ids.iter().map(String::as_str)
            .chain(extra.iter().take(MAX_EXCLUDED).map(String::as_str)).collect();
        if all.is_empty() {
            return (String::new(), Vec::new());
        }
        let placeholders = vec!["?"; all.len()].join(",");
        (format!(" AND delivery_id NOT IN ({placeholders})"), all.into_iter().map(DbValue::from).collect())
    }

    /// Decode a page, remembering undecodable ids. Returns whether any new
    /// id was recorded, i.e. whether a re-read would return more rows.
    fn decode(&self, rows: Vec<DbRow>) -> (Vec<PersistedMessageDelivery>, bool) {
        let mut learned = false;
        let mut decoded = Vec::with_capacity(rows.len());
        for row in rows {
            let id = row.get("delivery_id").and_then(DbValue::as_str).map(str::to_owned);
            match row_to_delivery(row) {
                Ok(delivery) => decoded.push(delivery),
                Err(_) => if let Some(id) = id {
                    let mut ids = self.0.lock().unwrap_or_else(|e| e.into_inner());
                    if ids.len() < MAX_EXCLUDED && ids.insert(id) { learned = true; }
                },
            }
        }
        (decoded, learned)
    }

    /// Run one scheduler scan page: `head` is `SELECT .. WHERE ..` with
    /// `params`, `tail` is `ORDER BY .. LIMIT ?` bound to `limit`. A page that
    /// revealed new corrupt rows is read once more, so it is refilled with
    /// healthy rows instead of returning short or empty.
    pub(crate) async fn scan(
        &self,
        db: &dyn DbPlugin,
        head: &str,
        params: Vec<DbValue>,
        tail: &str,
        limit: usize,
        extra: &[String],
    ) -> Result<Vec<PersistedMessageDelivery>, MessageDeliveryRepoError> {
        let mut decoded = Vec::new();
        for _ in 0..2 {
            let (clause, excluded) = self.exclusion(extra);
            let mut bound = params.clone();
            bound.extend(excluded);
            bound.push((limit as i64).into());
            let rows = db.query(DbStatement::with_params(format!("{head}{clause} {tail}"), bound)).await.map_err(storage)?;
            let (page, learned) = self.decode(rows);
            decoded = page;
            if !learned {
                break;
            }
        }
        Ok(decoded)
    }
}
