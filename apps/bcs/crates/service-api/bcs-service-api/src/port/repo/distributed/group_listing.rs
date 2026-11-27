//! The versioned public index of one published group (plan T8).
//!
//! The first query of a group runs one bounded collect over the accepted
//! task index (only resources an admission accepted are ever visible) and
//! MATERIALIZES the result as one immutable cut: filter, total and every
//! page's rows all read that same materialized version. Continuation pages
//! read only the materialized snapshot — never a re-collect, never a live
//! lookup — and are carried by server-signed cursors (bound to subject,
//! group, filter, sort, cut, last key and expiry). The caller revalidates
//! the reading authorization of every page outside this port; the port's
//! job is versioning consistency and cursor discipline, never authorization.

use super::common::StoreError;
use async_trait::async_trait;
use bcs_dist_model::{GroupId, PageToken, Sid, TaskScope, TaskStatus, TimestampMillis, Tid};
use std::num::NonZeroU32;

/// One normalized group listing query: the effective limits are the caller's;
/// the port never raises them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupIndexQuery {
    pub statuses: Vec<TaskStatus>,
    /// Rows per page; the caller caps this at the candidate profile bound.
    pub limit: NonZeroU32,
    /// Pin full row projections at the initial cut; restore only the served page.
    pub load_rows: bool,
}

/// One row of the materialized cut. Status and its update channel are part
/// of the cut: a later page never shows a row's newer live state.
#[derive(Debug, Clone, PartialEq)]
pub struct GroupIndexRow {
    pub tid: Tid,
    pub sid: Sid,
    pub status: TaskStatus,
    pub status_updated_at: TimestampMillis,
    /// Present only when the caller asked for loaded rows: the task's
    /// authoritative view at the original cut. An unavailable pinned version
    /// must fail the page, never fall back to a newer live projection.
    pub view: Option<bcs_dist_model::TaskView>,
}

/// One served page of a group listing.
#[derive(Debug, Clone, PartialEq)]
pub struct GroupIndexPage {
    pub rows: Vec<GroupIndexRow>,
    /// The filtered actual total of the cut — never this page's row count.
    pub total: u64,
    pub cut: String,
    pub next: Option<PageToken>,
}

#[async_trait]
pub trait GroupListingPort: Send + Sync {
    /// Materializes (or reuses) the current cut for (subject, group, filter)
    /// and serves its first page.
    async fn first_page(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        query: &GroupIndexQuery,
        now: TimestampMillis,
    ) -> Result<GroupIndexPage, StoreError>;

    /// Serves the next page of a previously materialized cut. The cursor is
    /// verified (signature, subject binding, filter, expiry) and the cut is
    /// only read back from the retained snapshot; a released cut is an
    /// explicit refusal, never a fallback to live state.
    async fn continue_page(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        token: &PageToken,
        load_rows: bool,
        now: TimestampMillis,
    ) -> Result<GroupIndexPage, StoreError>;
}
