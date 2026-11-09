//! Group Context repository implementations.

pub mod memory;
pub mod store;

pub use memory::MemoryGroupContextRepo;
pub use store::{
    GroupContextRepo, GroupContextStoreError, NewGroupContextEntry, SqlGroupContextStore,
    StoreResult,
};

/// Append context copies to the same transaction that creates the destination Session.
/// A short row count aborts the transaction if any selected version changed.
pub fn inheritance_step(
    entries: &[bcs_domain::GroupContextEntry],
    destination: &str,
    owner: &str,
) -> Result<bcs_db_api::DbTransactionStep, serde_json::Error> {
    use bcs_db_api::{DbStatement, DbTransactionStep, DbValue};

    let owners = serde_json::to_string(&bcs_domain::GroupContextOwners::new([owner]))?;
    let mut params = vec![destination.into(), owner.into(), owners.into()];
    let mut sources = Vec::with_capacity(entries.len());
    for entry in entries {
        sources.push("SELECT ? AS source_id, ? AS new_context_id");
        params.extend([
            DbValue::from(entry.id),
            DbValue::from(ulid::Ulid::new().to_string()),
        ]);
    }
    let sources = sources.join(" UNION ALL ");
    let sql = format!(
        "INSERT INTO bcs_group_contexts (context_id, name, scope_level, scope, content, creator, visible_to_json, collect_from_json, owners_json, is_slot, valid_from, valid_to, tx_time) \
         SELECT sources.new_context_id, source.name, source.scope_level, ?, source.content, ?, source.visible_to_json, source.collect_from_json, ?, source.is_slot, source.valid_from, source.valid_to, CURRENT_TIMESTAMP \
         FROM bcs_group_contexts source JOIN ({sources}) sources ON source.id = sources.source_id \
         WHERE source.scope_level = 'session' AND source.is_superseded = FALSE"
    );
    Ok(DbTransactionStep::ExecuteChecked {
        statement: DbStatement::with_params(sql, params),
        expected_affected_rows: entries.len() as u64,
    })
}
