//! The versioned public index of one published group (plan T8): the split
//! page of `task_reader.rs`, served by [`GroupListingPort`].
//!
//! The first query of a group runs one bounded collect over the accepted task
//! index and MATERIALIZES the result as one immutable cut (identity-digested,
//! create-if-absent); continuation pages read only that snapshot. The
//! per-session task reader stays untouched by group concerns, and no
//! protocol shape ever crosses this boundary.

use crate::page;
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
use std::ops::Bound;
use std::sync::Arc;
use bcs_store_api::zset::ZSetRange;


/// One collected row of the group cut, before materialization.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
struct CutRow {
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

/// The status channel of any stored task: the live snapshot's update time or
/// the archived record's finalization time.
fn status_channel(stored: &Stored) -> (String, i64, Tid, Sid, Option<TaskStatus>) {
    match stored {
        Stored::Live(snapshot) => (
            status_name(snapshot.content.status).to_owned(),
            snapshot.updated_at.0,
            snapshot.tid.clone(),
            snapshot.sid.clone(),
            Some(snapshot.content.status),
        ),
        Stored::Archived(record) | Stored::StrictArchived(record, _) => {
            let status = match record.status {
                TerminalStatus::Completed => TaskStatus::Completed,
                TerminalStatus::Failed => TaskStatus::Failed,
                TerminalStatus::Canceled => TaskStatus::Canceled,
                TerminalStatus::Rejected => TaskStatus::Rejected,
            };
            (
                status_name(status).to_owned(),
                record.finalized_at.0,
                record.tid.clone(),
                record.sid.clone(),
                Some(status),
            )
        }
    }
}

/// Bounded collect of one (scope, group) listing over the accepted-task
/// index. Only index members are candidates, so a task no admission accepted
/// (a crashed preparation) is invisible by construction. Cost per collect:
/// one index read per 100 candidates, one spec read per distinct session, one
/// task read per candidate (all batched), one snapshot read and one snapshot
/// CAS — never a checkpoint, directory or inbox read.
async fn collect_group(
    shared: &Arc<Shared>,
    scope: &TaskScope,
    group: &GroupId,
    statuses: &[TaskStatus],
) -> Result<Vec<CutRow>, StoreError> {
    let key = shared.layout.tasks(scope)?;
    let mut members: Vec<bcs_store_api::zset::ZSetEntry> = Vec::new();
    let mut after: Option<bcs_store_api::zset::ZSetEntry> = None;
    loop {
        let page = shared
            .zset
            .range(
                &key,
                &ZSetRange {
                    min: Bound::Unbounded,
                    max: Bound::Unbounded,
                    after: after.clone(),
                    limit: shared.config.read_batch,
                },
            )
            .await
            .map_err(|e| from_read(&e))?;
        let done = page.len() < shared.config.read_batch.get() as usize;
        after = page.last().cloned();
        members.extend(page);
        if done {
            break;
        }
    }
    // The frozen group of each distinct session, batched: one bounded read
    // per session, never per task.
    let mut sids: Vec<Sid> = Vec::new();
    for member in &members {
        let sid = Sid(member.member.rsplit_once('.').map_or_else(String::new, |(s, _)| s.to_owned()));
        if !sids.contains(&sid) {
            sids.push(sid);
        }
    }
    let lookups: Vec<_> = sids
        .iter()
        .map(|sid| read_session_group(shared.clone(), scope.clone(), sid.clone()))
        .collect();
    let resolved: Vec<Option<GroupId>> = futures::stream::iter(lookups)
        .buffered(LIST_READ_CONCURRENCY)
        .collect::<Vec<_>>()
        .await
        .into_iter()
        .collect::<Result<_, StoreError>>()?;
    // Source isolation: the group public index only ever carries the group
    // entrance namespace (plan T8) — a legacy session of the same subject and
    // group stays out of it.
    let group_of = |sid: &Sid| {
        if CreationSource::of_sid(sid) != Some(CreationSource::GroupA2a) {
            return None;
        }
        sids.iter()
            .position(|candidate| candidate == sid)
            .and_then(|index| resolved[index].clone())
    };
    // The authoritative stored view decides disclosure: scope match, the
    // strict-terminal authority cut and archive fallback all live in
    // `stored`. Batched reads, at most one page of served rows loaded later.
    let reads: Vec<_> = members
        .iter()
        .map(|member| read_stored(shared.clone(), scope.clone(), Tid(member.member.clone())))
        .collect();
    let stored_rows: Vec<Option<Stored>> = futures::stream::iter(reads)
        .buffered(LIST_READ_CONCURRENCY)
        .collect::<Vec<_>>()
        .await
        .into_iter()
        .collect::<Result<_, StoreError>>()?;
    let mut rows = Vec::new();
    for stored in stored_rows.into_iter().flatten() {
        let (status, updated_at, ..) = status_channel(&stored);
        let sid = match &stored {
            Stored::Live(snapshot) => snapshot.sid.clone(),
            Stored::Archived(record) | Stored::StrictArchived(record, _) => record.sid.clone(),
        };
        if group_of(&sid).as_ref() != Some(group) {
            continue;
        }
        if !statuses.is_empty()
            && status_of_name(&status).is_none_or(|status| !statuses.contains(&status))
        {
            continue;
        }
        rows.push(CutRow {
            tid: match &stored {
                Stored::Live(snapshot) => snapshot.tid.clone(),
                Stored::Archived(record) | Stored::StrictArchived(record, _) => record.tid.clone(),
            },
            sid,
            status,
            status_updated_at: updated_at,
        });
    }
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

async fn read_session_group(
    shared: Arc<Shared>,
    scope: TaskScope,
    sid: Sid,
) -> Result<Option<GroupId>, StoreError> {
    shared.session_group(&scope, &sid).await
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
) -> Result<(CutSnapshot, String), StoreError> {
    let rows = collect_group(shared, scope, group, statuses).await?;
    if rows.len() > 4096 {
        return Err(not_applied("group_cut_rows_budget"));
    }
    let rows_text = serde_json::to_string(&rows).map_err(|_| invalid("cut_encoding"))?;
    let digest = digest_fields(&[
        b"group_cut:v1",
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
        let (snapshot, cut) = materialize(shared, scope, group, &query.statuses, limit, now).await?;
        let total = snapshot.total;
        let rows: Vec<GroupIndexRow> = snapshot.rows[..(limit as usize).min(snapshot.rows.len())]
            .iter()
            .map(cut_row)
            .collect::<Result<_, _>>()?;
        let rows = if query.load_rows {
            load_views(shared, scope, rows).await?
        } else {
            rows
        };
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
        let rows: Vec<GroupIndexRow> = snapshot.rows[start..end]
            .iter()
            .map(cut_row)
            .collect::<Result<_, _>>()?;
        let rows = if load_rows {
            load_views(shared, scope, rows).await?
        } else {
            rows
        };
        let next = next_cursor(shared, &snapshot, &claims.cut, end)?;
        Ok(GroupIndexPage {
            rows,
            total: snapshot.total,
            cut: claims.cut,
            next,
        })
    }
}

/// Batched, page-scoped optional projections: one read per served row.
async fn load_views(
    shared: &Arc<Shared>,
    scope: &TaskScope,
    rows: Vec<GroupIndexRow>,
) -> Result<Vec<GroupIndexRow>, StoreError> {
    let reads: Vec<_> = rows
        .iter()
        .map(|row| read_stored(shared.clone(), scope.clone(), row.tid.clone()))
        .collect();
    let stored_rows: Vec<Option<Stored>> = futures::stream::iter(reads)
        .buffered(LIST_READ_CONCURRENCY)
        .collect::<Vec<_>>()
        .await
        .into_iter()
        .collect::<Result<_, StoreError>>()?;
    Ok(rows
        .into_iter()
        .zip(stored_rows)
        .map(|(row, stored)| GroupIndexRow {
            tid: row.tid,
            sid: row.sid,
            status: row.status,
            status_updated_at: row.status_updated_at,
            view: stored.map(stored_view),
        })
        .collect())
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
