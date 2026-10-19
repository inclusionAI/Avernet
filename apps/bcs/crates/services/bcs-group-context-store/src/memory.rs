use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{GroupContextEntry, GroupContextEntryMeta};
use tokio::sync::RwLock;

use crate::store::{GroupContextRepo, GroupContextStoreError, NewGroupContextEntry, StoreResult};

#[derive(Default, Clone)]
pub struct MemoryGroupContextRepo {
    state: Arc<RwLock<MemoryState>>,
}

#[derive(Default)]
struct MemoryState {
    next_id: i64,
    rows: Vec<GroupContextEntry>,
}

impl MemoryGroupContextRepo {
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl GroupContextRepo for MemoryGroupContextRepo {
    async fn create_active(&self, params: NewGroupContextEntry) -> StoreResult<GroupContextEntry> {
        let mut state = self.state.write().await;
        if state
            .rows
            .iter()
            .any(|row| row.name == params.name && row.scope == params.scope && !row.is_superseded)
        {
            return Err(GroupContextStoreError::DuplicateActive {
                name: params.name,
                scope: params.scope,
            });
        }
        state.next_id += 1;
        let row = row_from_new(state.next_id, params);
        state.rows.push(row.clone());
        Ok(row)
    }

    async fn supersede_active(
        &self,
        _name: &str,
        _scope: &str,
        expected_active_id: i64,
        params: NewGroupContextEntry,
    ) -> StoreResult<GroupContextEntry> {
        let mut state = self.state.write().await;

        // CAS guard: verify the expected active row is still active
        let old_index = state
            .rows
            .iter()
            .position(|row| row.id == expected_active_id && !row.is_superseded)
            .ok_or_else(|| GroupContextStoreError::Conflict {
                name: _name.to_string(),
                scope: _scope.to_string(),
            })?;

        let old = &state.rows[old_index];
        let context_id = old.context_id.clone();
        let tx_time = params.tx_time;

        // Mark old row as superseded
        state.rows[old_index].is_superseded = true;
        state.rows[old_index].gmt_superseded = Some(tx_time);
        state.rows[old_index].gmt_modified = tx_time;

        state.next_id += 1;
        let mut new_row = row_from_new(state.next_id, params);
        new_row.context_id = context_id;
        new_row.supersedes = Some(expected_active_id);
        state.rows.push(new_row.clone());
        Ok(new_row)
    }

    async fn get_active(&self, name: &str, scope: &str) -> StoreResult<Option<GroupContextEntry>> {
        Ok(self
            .state
            .read()
            .await
            .rows
            .iter()
            .find(|row| row.name == name && row.scope == scope && !row.is_superseded)
            .cloned())
    }

    async fn get_active_by_name_and_scopes(
        &self,
        name: &str,
        scopes: &[String],
    ) -> StoreResult<Vec<GroupContextEntry>> {
        Ok(self
            .state
            .read()
            .await
            .rows
            .iter()
            .filter(|row| {
                !row.is_superseded
                    && row.name == name
                    && scopes.iter().any(|scope| scope == &row.scope)
            })
            .cloned()
            .collect())
    }

    async fn list_active_meta_by_scopes(
        &self,
        scopes: &[String],
    ) -> StoreResult<Vec<GroupContextEntryMeta>> {
        Ok(self
            .state
            .read()
            .await
            .rows
            .iter()
            .filter(|row| !row.is_superseded && scopes.iter().any(|scope| scope == &row.scope))
            .map(meta_from_entry)
            .collect())
    }

    async fn list_versions(&self, context_id: &str) -> StoreResult<Vec<GroupContextEntry>> {
        Ok(self
            .state
            .read()
            .await
            .rows
            .iter()
            .filter(|row| row.context_id == context_id)
            .cloned()
            .collect())
    }
}

fn row_from_new(id: i64, params: NewGroupContextEntry) -> GroupContextEntry {
    GroupContextEntry {
        id,
        context_id: params.context_id,
        name: params.name,
        scope_level: params.scope_level,
        scope: params.scope,
        content: params.content,
        creator: params.creator,
        visible_to: params.visible_to,
        collect_from: params.collect_from,
        supersedes: params.supersedes,
        change_reason: params.change_reason,
        valid_from: params.valid_from,
        valid_to: params.valid_to,
        is_superseded: false,
        gmt_superseded: None,
        tx_time: params.tx_time,
        gmt_create: params.tx_time,
        gmt_modified: params.tx_time,
    }
}

fn meta_from_entry(entry: &GroupContextEntry) -> GroupContextEntryMeta {
    GroupContextEntryMeta {
        name: entry.name.clone(),
        scope_level: entry.scope_level,
        scope: entry.scope.clone(),
        visible_to: entry.visible_to.clone(),
        collect_from: entry.collect_from.clone(),
        gmt_create: entry.gmt_create,
        gmt_modified: entry.gmt_modified,
    }
}
