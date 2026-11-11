//! Real-MySQL upgrade fixture: valid legacy widths must survive 045 → 046.
use anyhow::Result;
use bcs_db_api::{DbPlugin, DbStatement, DbValue};

pub async fn seed_legacy(db: &dyn DbPlugin) -> Result<()> {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_friend_requests \
         (request_id, from_bot, to_bot, status, env, gmt_create, gmt_modified) \
         VALUES (?, ?, ?, 'pending', ?, '2026-11-01 00:00:00', '2026-11-02 00:00:00')",
        vec![DbValue::from("cutover-long-legacy-id"), DbValue::from("a".repeat(255)),
             DbValue::from("b".repeat(255)), DbValue::from("e".repeat(32))],
    )).await?;
    Ok(())
}

pub async fn verify(db: &dyn DbPlugin) -> Result<()> {
    let rows = db.query(DbStatement::new(
        "SELECT request_id, from_id, to_id, env, created_by, status FROM permission_requests \
         WHERE request_id = 'cutover-long-legacy-id'"
    )).await?;
    assert_eq!(rows.len(), 1);
    for (column, expected) in [("from_id", "a".repeat(255)), ("to_id", "b".repeat(255)),
        ("env", "e".repeat(32)), ("created_by", "a".repeat(255)), ("status", "pending".into())] {
        assert_eq!(rows[0].get_string(column)?, Some(expected));
    }
    // The migrated receiver must also fit when recording a later decision.
    db.execute(DbStatement::with_params(
        "UPDATE permission_requests SET decided_by = ? WHERE request_id = 'cutover-long-legacy-id'",
        vec![DbValue::from("b".repeat(255))],
    )).await?;
    let decided = db.query(DbStatement::new(
        "SELECT decided_by FROM permission_requests WHERE request_id = 'cutover-long-legacy-id'"
    )).await?;
    assert_eq!(decided[0].get_string("decided_by")?, Some("b".repeat(255)));
    bcs_db_migrations::ensure_friend_request_cutover(db).await?;
    Ok(())
}
