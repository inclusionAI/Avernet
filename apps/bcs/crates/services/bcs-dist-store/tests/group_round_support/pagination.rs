//! Executable pagination over an immutable list cut (S1.2 prototype).
//!
//! First page: run the existing bounded dual-collect (`list_cut`), sort rows
//! by `(status_updated_at DESC, tid ASC)`, apply the filter, then MATERIALIZE
//! the projection as `snapshot:<cut>` (create-if-absent via CAS; an existing
//! document must match the identity field by field).
//! Later pages read ONLY that snapshot document: never a re-collect, never a
//! live checkpoint, never a live Get ("后页不能重新读取实时状态").
//! Snapshot retention release or cursor expiry rejects continuation
//! explicitly; there is no silent live fallback.
//!
//! The cursor is a deterministic keyed MAC (HMAC-SHA256) over a canonical
//! serialization binding 主体 (tenant+principal)、群、筛选、排序、cut、末键、
//! 有效期. The signing key here is a constant prototype test key: production
//! key management (storage, rotation, KMS) is out of this layer by decision.
//!
//! # Cost bounds (prototype, per contract §5 有界)
//!
//! | 操作 | 最多读取 |
//! | --- | --- |
//! | 首页 | 双采集现有上界 (2 目录读, 每 Sid 2 checkpoint 读 + 1 投影读) + 1 ACL 读 + 1 快照读 (抢已有或确认空) + 1 快照 CAS |
//! | 续页 | 1 ACL 读 + 1 快照文档读, 0 次实时 checkpoint/目录/投影读; 返回行数 ≤ 页大小 |
//!
//! 快照行数本身受原型容量上界约束 (`max_sids` × `max_rounds_per_sid`,
//! 默认 16×16)。映射失败、投影落后或并发提交时按既有语义返回可重试
//! 依赖错误, 原型不做无限等待, 也绝不返回过时 total。
//!
//! Candidate profile v1 constants (100 rows / 300 s) are prototype-only test
//! values pending real encoding and index-retention validation; they are NOT
//! production defaults.
use super::*;
use super::lifecycle::digest;
use super::listing::CutRow;
use sha2::Sha256;

/// Candidate profile v1: max rows per page. 尚待真实编码和索引保留验证。
pub const CANDIDATE_PAGE_SIZE: usize = 100;
/// Candidate profile v1: snapshot cursor validity. 尚待真实生产验证。
pub const CANDIDATE_CURSOR_TTL_MILLIS: u64 = 300_000;

/// Contract sort order for List rows.
pub const SORT_STATUS_UPDATED_AT_DESC_TID_ASC: &str = "status_updated_at:desc,tid:asc";

const SNAPSHOT_PREFIX: &str = "snapshot:";
const ACL_KEY: &str = "t1-read-acl";
const GROUP: &str = "group";
/// Prototype-only HMAC key. Production key management is out of this layer.
const CURSOR_TEST_KEY: &[u8] = b"t1-prototype-cursor-test-key";

/// Prototype page-size / cursor-TTL knobs. `page_size` must be 1..=100
/// (candidate v1 "分页最多 100 行" enforced as a cap in `list_page`);
/// TTL is overridable for expiry boundary scenarios. These are prototype
/// test values, not production defaults.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PageProfile {
    pub page_size: usize,
    pub cursor_ttl_ms: u64,
}
impl Default for PageProfile {
    fn default() -> Self {
        Self { page_size: CANDIDATE_PAGE_SIZE,
            cursor_ttl_ms: CANDIDATE_CURSOR_TTL_MILLIS }
    }
}

/// List filter bound into the cursor and applied to the materialized cut.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ListFilter {
    All,
    Completed,
    Running,
}
impl ListFilter {
    pub fn as_str(&self) -> &'static str {
        match self { Self::All => "all", Self::Completed => "completed", Self::Running => "running" }
    }
}

/// Public row projection. Only identity, status and the sort channel are
/// disclosed; internal proofs, feeds and payloads stay in the cut evidence.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PublicRow {
    pub tid: Tid,
    pub request_id: RequestId,
    pub status: String,
    pub status_updated_at: u64,
}

/// One list page. `total`, row statuses and membership are all fixed by the
/// materialized cut for every page of the same query. `cut` identifies the
/// retained snapshot so retention release is observable.
#[derive(Debug, Clone)]
pub struct Page {
    pub rows: Vec<PublicRow>,
    pub total: usize,
    pub cut: String,
    pub next: Option<String>,
}

/// Cursor MAC-domain claims. Canonical serialization is serde_json over this
/// struct (fixed field order). Every bound field sits under the MAC: 主体、
/// 群、筛选、排序、cut、末键 (`last_status_at`+`last_tid`)、有效期.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct CursorClaims {
    pub tenant: String,
    pub principal: String,
    pub group: String,
    pub filter: String,
    pub sort: String,
    pub cut: String,
    pub last_status_at: u64,
    pub last_tid: String,
    pub expires_at_ms: u64,
}

// ---- cursor token encoding (server-signed) ----

fn sign_payload(payload: &[u8]) -> Result<String, StoreError> {
    type HmacSha256 = hmac::Hmac<Sha256>;
    use hmac::Mac as _;
    let mut mac = <HmacSha256 as hmac::Mac>::new_from_slice(CURSOR_TEST_KEY)
        .map_err(|_| error("cursor_key"))?;
    mac.update(payload);
    Ok(hex::encode(mac.finalize().into_bytes()))
}

fn split_token(token: &str) -> Result<(Vec<u8>, Vec<u8>), StoreError> {
    let (payload, mac) = token.split_once('.').ok_or_else(|| error("cursor_format"))?;
    let payload = hex::decode(payload).map_err(|_| error("cursor_format"))?;
    let mac = hex::decode(mac).map_err(|_| error("cursor_format"))?;
    Ok((payload, mac))
}

fn verify_signature(payload: &[u8], expected: &[u8]) -> Result<(), StoreError> {
    type HmacSha256 = hmac::Hmac<Sha256>;
    use hmac::Mac as _;
    let mut mac = <HmacSha256 as hmac::Mac>::new_from_slice(CURSOR_TEST_KEY)
        .map_err(|_| error("cursor_key"))?;
    mac.update(payload);
    mac.verify_slice(expected).map_err(|_| error("cursor_signature"))
}

fn token_from(claims: &CursorClaims) -> Result<String, StoreError> {
    let payload = serde_json::to_vec(claims).map_err(|_| error("encode"))?;
    let mac = sign_payload(&payload)?;
    Ok(format!("{}.{}", hex::encode(payload), mac))
}

// ---- test-vector helpers (never used by the listing path itself) ----

/// Unsigned claims parse for tamper vectors ONLY: production never exposes an
/// unsigned cursor parse; any continued use fails MAC verification anyway.
pub fn parse_cursor_claims(token: &str) -> Result<(CursorClaims, String), StoreError> {
    let (payload, mac) = split_token(token)?;
    let claims = serde_json::from_slice(&payload).map_err(|_| error("cursor_format"))?;
    Ok((claims, hex::encode(mac)))
}

/// Re-encode mutated claims while preserving an existing MAC: the canonical
/// forgery shape (no access to the signing key).
pub fn reissue_token_with_mac(claims: &CursorClaims, mac: &str) -> Result<String, StoreError> {
    let payload = serde_json::to_vec(claims).map_err(|_| error("encode"))?;
    Ok(format!("{}.{}", hex::encode(payload), mac))
}

// ---- fixture authorization state ----

impl Prototype {
    /// Logical clock read for cursor validity windows.
    fn now_ms(&self) -> u64 { self.clock.load(std::sync::atomic::Ordering::SeqCst) }
    /// Advance the logical clock (tests only; production uses real clocks).
    pub fn advance_clock(&self, ms: u64) {
        self.clock.fetch_add(ms, std::sync::atomic::Ordering::SeqCst);
    }

    /// Grant list read on the fixture group for a principal (idempotent).
    pub async fn grant_read(&self, principal: &str) -> Result<(), StoreError> {
        for _ in 0..8 {
            let (version, mut rows) = match self.document(ACL_KEY).await? {
                Some((v, value)) => (Some(v), value.as_array().cloned()
                    .ok_or_else(|| error("acl"))?),
                None => (None, vec![]),
            };
            if let Some(row) = rows.iter_mut().find(|row| row["principal"] == json!(principal)) {
                let groups = row["groups"].as_array_mut().ok_or_else(|| error("acl"))?;
                if !groups.contains(&json!("group")) { groups.push(json!("group")); }
                return if self.replace(ACL_KEY, version, Value::Array(rows)).await? { Ok(()) }
                    else { continue };
            }
            rows.push(json!({"principal": principal, "groups": ["group"]}));
            if self.replace(ACL_KEY, version, Value::Array(rows)).await? { return Ok(()) }
        }
        Err(dependency("acl_contention"))
    }

    /// Revoke list read for a principal. Old cursors stop disclosing on the
    /// next page turn (翻页重验), they are not silently honored.
    pub async fn revoke_read(&self, principal: &str) -> Result<(), StoreError> {
        for _ in 0..8 {
            let Some((version, value)) = self.document(ACL_KEY).await? else {
                return Ok(());
            };
            let rows = value.as_array().cloned().ok_or_else(|| error("acl"))?;
            if !rows.iter().any(|row| row["principal"] == json!(principal)) { return Ok(()); }
            let retained: Vec<Value> = rows.into_iter()
                .filter(|row| row["principal"] != json!(principal)).collect();
            if self.replace(ACL_KEY, Some(version), Value::Array(retained)).await? {
                return Ok(());
            }
        }
        Err(dependency("acl_contention"))
    }

    /// Current read permission check for List 首页和每一次续页.
    async fn check_read(&self, requester: &TaskScope, group: &str) -> Result<(), StoreError> {
        let rows = match self.document(ACL_KEY).await? {
            Some((_, value)) => value.as_array().cloned().ok_or_else(|| error("acl"))?,
            None => vec![],
        };
        let allowed = rows.iter().any(|row| row["principal"] == json!(requester.principal.0)
            && row["groups"].as_array().is_some_and(|groups|
                groups.contains(&json!(group))));
        if allowed { Ok(()) } else { Err(error("unauthorized")) }
    }
}

/// Public row projection of one cut row.
fn public_row(row: &CutRow) -> PublicRow {
    let view = &row.view;
    PublicRow {
        tid: view.accepted.input.key.tid.clone(),
        request_id: view.accepted.request_id.clone(),
        status: if view.terminal.is_some() { "completed" } else { "running" }.into(),
        status_updated_at: row.status_updated_at,
    }
}

impl Prototype {
    /// First (or fresh) List page: bounded dual-collect, then materialize the
    /// immutable cut snapshot keyed by cut identity, then slice page one.
    pub async fn list_page(&self, requester: &TaskScope, profile: &PageProfile,
        filter: &ListFilter) -> Result<Page, StoreError>
    {
        // 分页最多 100 行 is a maximum, not a default: the candidate cap is
        // enforced here, so a page can never exceed the documented bound.
        if profile.page_size == 0 || profile.page_size > CANDIDATE_PAGE_SIZE {
            return Err(error("page_size"));
        }
        // Current read permission gates the new query.
        self.check_read(requester, GROUP).await?;
        // Bounded dual-collect: projection lag or concurrent commit surface as
        // retryable dependency errors here, never as a stale or missing total.
        let cut = self.list_cut().await?;
        // Fixed contract order: status_updated_at DESC, ties broken tid ASC.
        let mut rows = cut.rows;
        rows.sort_by(|a, b| b.status_updated_at.cmp(&a.status_updated_at)
            .then_with(|| a.view.accepted.input.key.tid.0
                .cmp(&b.view.accepted.input.key.tid.0)));
        let filtered: Vec<&CutRow> = rows.iter().filter(|r| match filter {
            ListFilter::All => true,
            ListFilter::Completed => r.view.terminal.is_some(),
            ListFilter::Running => r.view.terminal.is_none(),
        }).collect();
        // 按 Tid 唯一 inside one cut is a contract invariant; a violation is an
        // error, never silently deduplicated rows.
        let mut unique = std::collections::HashSet::new();
        for r in &filtered {
            if !unique.insert(r.view.accepted.input.key.tid.0.as_str()) {
                return Err(error("cut_duplicate_tid"));
            }
        }
        let public: Vec<PublicRow> = filtered.iter().map(|r| public_row(r)).collect();
        // Cut identity binds subject/group/filter/sort/page size and the row
        // projection; the retained snapshot document is keyed by it.
        let identity = json!({
            "tenant": requester.tenant.0, "principal": requester.principal.0,
            "group": GROUP, "filter": filter.as_str(),
            "sort": SORT_STATUS_UPDATED_AT_DESC_TID_ASC,
            "page_size": profile.page_size,
            "rows": &public,
        });
        let cut_id = digest(&identity)?.0;
        let expires_at_ms = self.now_ms() + profile.cursor_ttl_ms;
        let mut content = identity.clone();
        content["total"] = json!(public.len());
        content["created_ms"] = json!(self.now_ms());
        content["expires_at_ms"] = json!(expires_at_ms);
        // Materialize create-if-absent; an existing document must carry the
        // identical identity (timing fields may differ across re-issues).
        let key = format!("{SNAPSHOT_PREFIX}{cut_id}");
        let mut ready = false;
        for _ in 0..8 {
            if let Some((_, existing)) = self.document(&key).await? {
                for field in ["tenant", "principal", "group", "filter", "sort",
                    "page_size", "rows"] {
                    if existing[field] != identity[field] {
                        return Err(error("snapshot_conflict"));
                    }
                }
                ready = true;
                break;
            }
            if self.replace(&key, None, content.clone()).await? { ready = true; break; }
        }
        if !ready { return Err(dependency("snapshot_contention")); }
        let take = public.len().min(profile.page_size);
        let page_rows = public[..take].to_vec();
        let next = if public.len() > take {
            let last = page_rows.last().unwrap();
            let claims = CursorClaims {
                tenant: requester.tenant.0.clone(),
                principal: requester.principal.0.clone(),
                group: GROUP.into(), filter: filter.as_str().into(),
                sort: SORT_STATUS_UPDATED_AT_DESC_TID_ASC.into(),
                cut: cut_id.clone(), last_status_at: last.status_updated_at,
                last_tid: last.tid.0.clone(), expires_at_ms };
            Some(token_from(&claims)?)
        } else { None };
        Ok(Page { rows: page_rows, total: public.len(), cut: cut_id, next })
    }

    /// Continuation page: verify the cursor MAC and every bound, re-check the
    /// CURRENT read permission, then slice only from the retained snapshot.
    pub async fn continue_page(&self, requester: &TaskScope, token: &str)
        -> Result<Page, StoreError>
    {
        let (payload, mac) = split_token(token)?;
        verify_signature(&payload, &mac)?;
        let claims: CursorClaims = serde_json::from_slice(&payload)
            .map_err(|_| error("cursor_format"))?;
        // 游标绑定主体: the token only continues in the hands of its subject.
        if claims.tenant != requester.tenant.0 || claims.principal != requester.principal.0 {
            return Err(error("cursor_subject"));
        }
        // 到期拒绝: explicit, no grace and no live re-derivation.
        if self.now_ms() >= claims.expires_at_ms { return Err(error("cursor_expired")); }
        // 翻页重验: an old cut must not bypass a fresh revocation.
        let bound = TaskScope { tenant: TenantId(claims.tenant.clone()),
            principal: PrincipalId(claims.principal.clone()) };
        self.check_read(&bound, &claims.group).await?;
        // Only the materialized immutable cut serves continuation. A released
        // retention window rejects explicitly; there is no live fallback.
        let Some((_, snapshot)) = self.document(&format!("{SNAPSHOT_PREFIX}{}", claims.cut)).await?
        else { return Err(dependency("snapshot_released")); };
        if snapshot["group"] != json!(claims.group) || snapshot["filter"] != json!(claims.filter)
            || snapshot["sort"] != json!(claims.sort) {
            return Err(error("cursor_bindings"));
        }
        let rows: Vec<PublicRow> = snapshot["rows"].as_array()
            .ok_or_else(|| error("snapshot_rows"))?.iter().cloned()
            .map(decode).collect::<Result<Vec<_>, _>>()?;
        let total = snapshot["total"].as_u64().ok_or_else(|| error("snapshot_rows"))? as usize;
        let page_size = snapshot["page_size"].as_u64()
            .ok_or_else(|| error("snapshot_rows"))? as usize;
        let Some(index) = rows.iter().position(|r|
            r.status_updated_at == claims.last_status_at && r.tid.0 == claims.last_tid)
        else { return Err(error("cursor_last_key")); };
        let remaining = &rows[index + 1..];
        let take = remaining.len().min(page_size);
        let page_rows = remaining[..take].to_vec();
        let next = if remaining.len() > take {
            let last = page_rows.last().unwrap();
            let claims = CursorClaims {
                tenant: claims.tenant.clone(), principal: claims.principal.clone(),
                group: claims.group.clone(), filter: claims.filter.clone(),
                sort: claims.sort.clone(), cut: claims.cut.clone(),
                last_status_at: last.status_updated_at, last_tid: last.tid.0.clone(),
                expires_at_ms: claims.expires_at_ms };
            Some(token_from(&claims)?)
        } else { None };
        Ok(Page { rows: page_rows, total, cut: claims.cut.clone(), next })
    }

    /// Snapshot retention release (version-gated delete). Later continuation
    /// with a released cut fails explicitly instead of falling back to live
    /// state. Recreating the same cut re-materializes the snapshot.
    pub async fn release_snapshot(&self, cut: &str) -> Result<(), StoreError> {
        self.delete_doc(&format!("{SNAPSHOT_PREFIX}{cut}")).await
    }
}
