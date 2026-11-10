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
    let mut params = Vec::with_capacity(entries.len() * 3 + 3);
    for entry in entries {
        params.push(DbValue::from(entry.id));
        params.push(DbValue::from(ulid::Ulid::new().to_string()));
    }
    params.extend([DbValue::from(destination), owner.into(), owners.into()]);
    params.extend(entries.iter().map(|entry| DbValue::from(entry.id)));
    let cases = vec!["WHEN ? THEN ?"; entries.len()].join(" ");
    let ids = vec!["?"; entries.len()].join(", ");
    let sql = format!(
        "INSERT INTO bcs_group_contexts (context_id, name, scope_level, scope, content, creator, visible_to_json, collect_from_json, owners_json, is_slot, valid_from, valid_to, tx_time) \
         SELECT CASE id {cases} END, name, scope_level, ?, content, ?, visible_to_json, collect_from_json, ?, is_slot, valid_from, valid_to, CURRENT_TIMESTAMP \
         FROM bcs_group_contexts WHERE id IN ({ids}) AND is_superseded = FALSE AND scope_level = 'session'"
    );
    Ok(DbTransactionStep::ExecuteChecked {
        statement: DbStatement::with_params(sql, params),
        expected_affected_rows: entries.len() as u64,
    })
}
