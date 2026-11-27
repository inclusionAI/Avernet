//! The versioned public index of one published group (plan T8): the split
//! page of `task_reader.rs`, served by [`GroupListingPort`].
//!
//! The first query of a group runs one bounded collect over the accepted task
//! index and MATERIALIZES the result as one immutable cut (identity-digested,
//! create-if-absent); continuation pages read only that snapshot. The
//! per-session task reader stays untouched by group concerns, and no
//! protocol shape ever crosses this boundary.

use crate::{page, group_projection::{self, PendingBlock, ProjectionRef}};
use bcs_store_api::kv::KvStorePlugin;
use crate::task_reader::{
    Shared, StoreTaskReader, not_applied, read_stored, status_key, status_name, status_of_name,
    stored_view, Stored, LIST_READ_CONCURRENCY, invalid,
};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::error::from_read;
use bcs_dist_store::identity::{digest_fields, hex_of};
use bcs_service_api::application::dist_gateway::CreationSource;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::group_listing::*;
use bcs_service_api::port::repo::distributed::group_round::RoundBudgetProfile;
use bcs_store_api::WriteOutcome;
use futures::StreamExt;
use std::{collections::{BTreeMap, BTreeSet}, ops::Bound};
use std::sync::Arc;
use bcs_store_api::zset::ZSetRange;


/// One collected row of the group cut, before materialization.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub(crate) struct CutRow {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) projection: Option<ProjectionRef>,
    tid: Tid,
    sid: Sid,
    status: String,
    status_updated_at: i64,
}

/// The materialized snapshot of one group listing: created-if-absent under
/// its identity digest; a matching prior document is reused as is.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
struct CutSnapshot {
    schema_version: u32,
    tenant: String,
    principal: String,
    group: String,
    statuses: String,
    sort: String,
    page_size: u32,
    rows: Vec<CutRow>,
    total: u64,
    created_ms: i64,
    expires_at_ms: i64,
}

fn cut_row(row: &CutRow) -> Result<GroupIndexRow, StoreError> {
    Ok(GroupIndexRow {
        tid: row.tid.clone(),
        sid: row.sid.clone(),
        status: status_of_name(&row.status).ok_or_else(|| invalid("cut_encoding"))?,
        status_updated_at: TimestampMillis(row.status_updated_at),
        view: None,
    })
}

/// Scan at most 4096 subject-index candidates; never publish a partial total.
/// Resolve each strict session once and consult the authoritative reader only
/// for this group. Strict reads may also consult checkpoint/archive proofs.
const MAX_CUT_CANDIDATES: usize = 4096;

async fn collect_group(
    shared: &Arc<Shared>,
    scope: &TaskScope,
    group: &GroupId,
    statuses: &[TaskStatus],
    projections: Option<&dyn KvStorePlugin>,
) -> Result<Vec<CutRow>, StoreError> {
    let key = shared.layout.tasks(scope)?;
    let mut members: Vec<bcs_store_api::zset::ZSetEntry> = Vec::new();
    let mut after: Option<bcs_store_api::zset::ZSetEntry> = None;
    loop {
        let remaining = MAX_CUT_CANDIDATES - members.len();
        let batch = shared.config.read_batch.min(std::num::NonZeroU32::new((remaining + 1) as u32).unwrap_or(std::num::NonZeroU32::MIN));
        let page = shared
            .zset
            .range(
                &key,
                &ZSetRange {
                    min: Bound::Unbounded,
                    max: Bound::Unbounded,
                    after: after.clone(),
                    limit: batch,
                },
            )
            .await
            .map_err(|e| from_read(&e))?;
        let done = page.len() < batch.get() as usize;
        after = page.last().cloned();
        if page.len() > MAX_CUT_CANDIDATES - members.len() {
            return Err(not_applied("group_cut_scan_budget"));
        }
        members.extend(page);
        if done {
            break;
        }
    }
    // The frozen group of each distinct session, batched: one bounded read
    // per session, never per task.
    let member_sid = |member: &str| member.rsplit_once('.').map(|(sid, _)| Sid(sid.to_owned()));
    let sids: BTreeSet<_> = members.iter().filter_map(|member| member_sid(&member.member))
        .filter(|sid| CreationSource::of_sid(sid) == Some(CreationSource::GroupA2a)).collect();
    let lookup = |sid: Sid| async move {
        let group = shared.session_group(scope, &sid).await?;
        Ok((sid, group))
    };
    let groups: BTreeMap<_, _> = futures::stream::iter(sids.into_iter().map(lookup))
        .buffered(LIST_READ_CONCURRENCY).collect::<Vec<_>>().await.into_iter()
        .collect::<Result<_, StoreError>>()?;
    let in_group = |sid: &Sid| groups.get(sid).and_then(Option::as_ref) == Some(group);
    let reads = members.into_iter().filter(|member| member_sid(&member.member).as_ref().is_some_and(&in_group))
        .map(|member| read_stored(shared.clone(), scope.clone(), Tid(member.member)));
    let mut stored_rows = futures::stream::iter(reads).buffered(LIST_READ_CONCURRENCY);
    let mut rows = Vec::new();
    let mut block = PendingBlock::default();
    while let Some(stored) = stored_rows.next().await {
        let Some(stored) = stored? else { continue; };
        let updated_at = match &stored {
            Stored::Live(snapshot) => snapshot.updated_at,
            Stored::Archived(record) | Stored::StrictArchived(record, _) => record.finalized_at,
        };
        let view = stored_view(stored);
        if view.scope != *scope || !in_group(&view.sid) { continue; }
        if !statuses.is_empty() && !statuses.contains(&view.content.status) { continue; }
        rows.push(CutRow {
            tid: view.tid.clone(), sid: view.sid.clone(),
            status: status_name(view.content.status).to_owned(), status_updated_at: updated_at.0,
            projection: None,
        });
        if let Some(store) = projections {
            block.push(shared, store, &mut rows, view).await?;
        }
    }
    if let Some(store) = projections { block.flush(shared, store, &mut rows).await?; }
    // Fixed contract order: status_updated_at DESC, ties broken tid ASC. One
    // Tid appears at most once (index members are unique); a violation of
    // that invariant is an error, never silently deduplicated rows.
    rows.sort_by(|a, b| {
        b.status_updated_at
            .cmp(&a.status_updated_at)
            .then_with(|| a.tid.0.cmp(&b.tid.0))
    });
    let mut unique = std::collections::HashSet::new();
    for row in &rows {
        if !unique.insert(row.tid.0.as_str()) {
            return Err(invalid("cut_duplicate_tid"));
        }
    }
    Ok(rows)
}

/// Read-or-materialize the cut snapshot of one query identity. The digest
/// binds subject, group, filter, sort, page size and the complete row set;
/// an existing document under the same digest must match field for field.
async fn materialize(
    shared: &Arc<Shared>,
    scope: &TaskScope,
    group: &GroupId,
    statuses: &[TaskStatus],
    page_size: u32,
    now: TimestampMillis,
    projections: Option<&dyn KvStorePlugin>,
) -> Result<(CutSnapshot, String), StoreError> {
    let rows = collect_group(shared, scope, group, statuses, projections).await?;
    let rows_text = serde_json::to_string(&rows).map_err(|_| invalid("cut_encoding"))?;
    let digest = digest_fields(&[
        if projections.is_some() { b"group_cut:projected:v2" } else { b"group_cut:v1" },
        scope.tenant.0.as_bytes(),
        scope.principal.0.as_bytes(),
        group.0.as_bytes(),
        status_key(statuses).as_bytes(),
        page::GROUP_SORT.as_bytes(),
        page_size.to_string().as_bytes(),
        rows_text.as_bytes(),
    ]);
    let cut = hex_of(&digest).to_owned();
    let now = now.0;
    let snapshot = CutSnapshot {
        schema_version: 1,
        tenant: scope.tenant.0.clone(),
        principal: scope.principal.0.clone(),
        group: group.0.clone(),
        statuses: status_key(statuses),
        sort: page::GROUP_SORT.to_owned(),
        page_size,
        total: u64::try_from(rows.len()).unwrap_or(u64::MAX),
        rows,
        created_ms: now,
        expires_at_ms: now + (page::CURSOR_LIFETIME_MILLIS as i64),
    };
    let key = format!("{}:group-cut:{}", shared.layout.namespace_identity(), cut);
    let value = serde_json::to_value(&snapshot).map_err(|_| invalid("cut_encoding"))?;
    let outcome = shared
        .documents
        .create_document(&key, &value)
        .await
        .map_err(|e| from_read(&e))?;
    if matches!(outcome, WriteOutcome::Applied { .. }) {
        return Ok((snapshot, cut));
    }
    // The cut already exists: the digest covered the identity fields, so the
    // stored document must match field for field (timing aside).
    let doc = shared
        .documents
        .read_document(&key)
        .await
        .map_err(|e| from_read(&e))?
        .ok_or_else(|| not_applied("cut_materialization"))?;
    let existing: CutSnapshot =
        serde_json::from_value(doc.value).map_err(|_| invalid("cut_encoding"))?;
    if existing.tenant != snapshot.tenant
        || existing.principal != snapshot.principal
        || existing.group != snapshot.group
        || existing.statuses != snapshot.statuses
        || existing.sort != snapshot.sort
        || existing.page_size != snapshot.page_size
        || existing.rows != snapshot.rows
    {
        return Err(not_applied("cut_conflict"));
    }
    if now >= existing.expires_at_ms {
        // Only the lease on this immutable row set changes. Old signed cursors
        // retain their own expiry and cannot regain validity after renewal.
        let outcome = shared.documents.replace_document(&key, doc.version, &value)
            .await.map_err(|e| from_read(&e))?;
        if outcome == WriteOutcome::ConditionNotMet {
            return Err(not_applied("cut_refresh_contention"));
        }
        return Ok((snapshot, cut));
    }
    Ok((existing, cut))
}

#[async_trait]
impl GroupListingPort for StoreTaskReader {
    async fn first_page(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        query: &GroupIndexQuery,
        now: TimestampMillis,
    ) -> Result<GroupIndexPage, StoreError> {
        let shared = &self.shared;
        let profile = RoundBudgetProfile::v1();
        let limit = query.limit.get();
        if limit == 0 || limit > profile.max_page_rows {
            return Err(invalid("page_size_budget"));
        }
        if query.statuses.len() > 8 {
            return Err(invalid("status_filter_budget"));
        }
        // One policy for every page shape (plan T8): a reader without a
        // configured signing key serves no group listing at all — fail fast,
        // before any collect or cut materialization, rather than answering a
        // single page that then cannot continue.
        if shared
            .config
            .cursor_secret
            .as_ref()
            .is_none_or(|key| key.is_empty())
        {
            return Err(not_applied("cursor_secret_unconfigured"));
        }
        let projections = self.projections(query.load_rows)?;
        let (snapshot, cut) = materialize(shared, scope, group, &query.statuses, limit, now, projections).await?;
        let total = snapshot.total;
        let frozen = &snapshot.rows[..(limit as usize).min(snapshot.rows.len())];
        let rows = self.page_rows(scope, frozen, projections).await?;
        let next = next_cursor(shared, &snapshot, &cut, (limit as usize).min(snapshot.rows.len()))?;
        Ok(GroupIndexPage { rows, total, cut, next })
    }

    async fn continue_page(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        token: &PageToken,
        load_rows: bool,
        now: TimestampMillis,
    ) -> Result<GroupIndexPage, StoreError> {
        let shared = &self.shared;
        let claims = page::decode(token, &shared.config.cursor_secret)?;
        // The group surface has no caller-derived filter here: the filter
        // binding is real below, against the materialized snapshot the cursor
        // pages through (`None` tells `verify` to skip the vacuous check).
        page::verify(
            &claims,
            "group",
            &scope.tenant.0,
            &scope.principal.0,
            &group.0,
            None,
            page::GROUP_SORT,
            now.0,
        )?;
        let key = format!("{}:group-cut:{}", shared.layout.namespace_identity(), claims.cut);
        let doc = shared
            .documents
            .read_document(&key)
            .await
            .map_err(|e| from_read(&e))?
            .ok_or_else(|| not_applied("cut_released"))?;
        let snapshot: CutSnapshot =
            serde_json::from_value(doc.value).map_err(|_| invalid("cut_encoding"))?;
        if snapshot.tenant != claims.tenant
            || snapshot.principal != claims.principal
            || snapshot.group != claims.binding
            || snapshot.statuses != claims.statuses
            || snapshot.sort != claims.sort
        {
            return Err(invalid("cursor_binding"));
        }
        if now.0 >= snapshot.expires_at_ms {
            return Err(invalid("cut_expired"));
        }
        let Some(offset) = snapshot
            .rows
            .iter()
            .position(|row| row.status_updated_at == claims.last_score && row.tid.0 == claims.last_member)
        else {
            return Err(invalid("cursor_last_key"));
        };
        let start = offset + 1;
        let end = (start + snapshot.page_size as usize).min(snapshot.rows.len());
        let rows = self.page_rows(scope, &snapshot.rows[start..end], self.projections(load_rows)?).await?;
        let next = next_cursor(shared, &snapshot, &claims.cut, end)?;
        Ok(GroupIndexPage {
            rows,
            total: snapshot.total,
            cut: claims.cut,
            next,
        })
    }
}

impl StoreTaskReader {
    fn projections(&self, requested: bool) -> Result<Option<&dyn KvStorePlugin>, StoreError> {
        if !requested { return Ok(None); }
        self.projection_store.as_deref().map(Some).ok_or_else(|| not_applied("group_projections_unconfigured"))
    }

    async fn page_rows(&self, scope: &TaskScope, frozen: &[CutRow], projections: Option<&dyn KvStorePlugin>)
        -> Result<Vec<GroupIndexRow>, StoreError> {
        let mut rows = frozen.iter().map(cut_row).collect::<Result<Vec<_>, _>>()?;
        if let Some(store) = projections {
            group_projection::restore(&self.shared, store, scope, frozen, &mut rows).await?;
        }
        Ok(rows)
    }
}

/// The continuation cursor after `end` served rows; `None` when the cut ends.
fn next_cursor(
    shared: &Shared,
    snapshot: &CutSnapshot,
    cut: &str,
    end: usize,
) -> Result<Option<PageToken>, StoreError> {
    if end >= snapshot.rows.len() || end == 0 {
        return Ok(None);
    }
    let last = &snapshot.rows[end - 1];
    page::encode(
        &page::CursorClaims {
            surface: "group".to_owned(),
            tenant: snapshot.tenant.clone(),
            principal: snapshot.principal.clone(),
            binding: snapshot.group.clone(),
            statuses: snapshot.statuses.clone(),
            sort: page::GROUP_SORT.to_owned(),
            cut: cut.to_owned(),
            last_score: last.status_updated_at,
            last_member: last.tid.0.clone(),
            expires_at_ms: snapshot.expires_at_ms,
        },
        &shared.config.cursor_secret,
    )
    .map(Some)
    .map_err(|_| not_applied("cursor_secret_unconfigured"))
}
