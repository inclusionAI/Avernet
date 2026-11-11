//! Read-only activation check shared by the SQLite and MySQL bootstraps.
use bcs_db_api::{DbError, DbPlugin, DbResult, DbStatement};

/// Refuse the new request reader until every legacy request has an ID-preserving
/// counterpart. Decisions may differ after cutover, so compare identity, not
/// mutable status. One anti-join query scans legacy IDs using the new ID index
/// and returns at most one row. No data is materialized in application memory.
pub async fn ensure_friend_request_cutover(db: &dyn DbPlugin) -> DbResult<()> {
    let missing = db.query(DbStatement::new(
        "SELECT 1 AS missing FROM bcs_friend_requests legacy \
         WHERE NOT EXISTS (SELECT 1 FROM permission_requests migrated \
           WHERE migrated.request_id = legacy.request_id AND migrated.env = legacy.env \
             AND migrated.from_id = legacy.from_bot AND migrated.to_id = legacy.to_bot \
             AND migrated.request_kind = 'connect') LIMIT 1"
    )).await?;
    if !missing.is_empty() {
        return Err(DbError::InvalidInput(
            "legacy friend requests are not migrated: stop legacy writers and apply \
             MySQL migration 046 / SQLite migration 044 before enabling connect-backed readers".into(),
        ));
    }
    Ok(())
}
