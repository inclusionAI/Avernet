//! `bcs-storage-baas`: `StoragePlugin` impl for the baas Session File Sharing
//! API v1.1. Uploads bypass BCS (client PUTs to OSS direct URLs), complete is
//! sync-to-DONE, downloads go through sync `POST /share-link`, delete is by
//! transfer_id. See design-baas-plugin spec.

pub mod config;
pub mod error;
pub mod factory;
pub mod handle;

pub use factory::BaasStoragePluginFactory;
pub use handle::{BaasPendingHandle, BaasReadyHandle};

use async_trait::async_trait;
use bcs_domain::{ActorKind, ActorRef};
use bcs_storage_api::{
    ByteStream, ClientUploadTarget, PresignGetTicket, PreparedUpload, StorageCapabilities,
    StorageError, StorageHandle, StorageHealth, StorageObjectMeta, StoragePlugin, UploadHandle,
    UploadMode, UploadPartUrl, UploadPrepareRequest,
};

use crate::config::BaasConfig;

pub struct BaasStoragePlugin {
    cfg: BaasConfig,
    caps: StorageCapabilities,
    http: reqwest::Client,
}

impl BaasStoragePlugin {
    pub fn new(cfg: BaasConfig, max_object_size: u64) -> Self {
        let caps = StorageCapabilities {
            supports_presign_put: true,
            supports_presign_download: true,
            supports_stream_put: true,
            supports_stream_get: true,
            max_object_size,
        };
        let builder = reqwest::Client::builder().timeout(cfg.http_timeout);
        // Attach auth headers via a default-headers middleware substitute: store on cfg, applied per-request in client layer.
        let http = builder.build().expect("reqwest client build");
        Self { cfg, caps, http }
    }

    /// Build the baas session root path (files/transfers are siblings under it):
    /// {endpoint}/api/v1/sessions/{tenant}/{session_id percent-encoded}.
    /// tenant and session_id are percent-encoded to be safe path segments.
    fn session_path_root(&self, session_id: &str) -> String {
        format!(
            "{}/api/v1/sessions/{}/{}",
            self.cfg.endpoint.trim_end_matches('/'),
            percent_encode_path(&self.cfg.tenant),
            percent_encode_path(session_id)
        )
    }

    /// Build the baas files base path: {session_path_root}/files.
    fn base_for_session(&self, session_id: &str) -> String {
        format!("{}/files", self.session_path_root(session_id))
    }

    fn auth(&self, req: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        let mut r = req;
        for (k, v) in &self.cfg.auth_headers {
            r = r.header(k, v);
        }
        r
    }
}

/// percent-encode a single path segment (session_id / tenant / transfer_id).
/// baas session_id may contain ':' (e.g. bcs_grp_<uuid>:<hex>) which is not
/// path-segment-safe — must encode ':' (and `/` etc.). `_`/`-` are safe and
/// kept verbatim (NON_ALPHANUMERIC would encode `_`→`%5F`, breaking the
/// readable `bcs_grp_...` form — so use a block-set that encodes only
/// separators/unreserved-unsafe chars, NOT a permissive allow-set).
///
/// Encoding set: start from RFC 3986 path-segment chars and **add `:` to the
/// encode-set** (a path sub-delim `:` is legal per RFC but we choose to encode
/// it so baas routers never treat it as a delimiter). Concretely use
/// `percent_encoding::AsciiSet` extended from `NON_ALPHANUMERIC`'s complement:
/// encode `:` `/` `?` `#` `[` `]` `@` `!` `$` `&` `'` `(` `)` `*` `+` `,` `;`
/// `=` `%` and space; **keep** `A-Za-z0-9 - _ . ~`.
fn percent_encode_path(s: &str) -> String {
    use percent_encoding::{utf8_percent_encode, AsciiSet, CONTROLS};
    // 0x3A = ':', 0x2F = '/', 0x3F = '?', 0x23 = '#', 0x5B/5D = '[',']', 0x40 = '@',
    // sub-delims: 0x21 '!', 0x24 '$', 0x26 '&', 0x27 ''', 0x28/29 '(',')', 0x2A '*',
    // 0x2B '+', 0x2C ',', 0x3B ';', 0x3D '=', 0x25 '%', 0x20 space.
    const ENCODE_SET: &AsciiSet = &CONTROLS
        .add(b':').add(b'/').add(b'?').add(b'#').add(b'[').add(b']').add(b'@')
        .add(b'!').add(b'$').add(b'&').add(b'\'').add(b'(').add(b')').add(b'*')
        .add(b'+').add(b',').add(b';').add(b'=').add(b'%').add(b' ');
    utf8_percent_encode(s, ENCODE_SET).to_string()
}

#[cfg(test)]
mod tests {
    use super::percent_encode_path;

    #[test]
    fn colon_encoded_underscore_kept() {
        // session_id 形如 bcs_grp_abc:cdf28232：':' 编码、'_' 保留（可读 bcs_grp_...）。
        assert_eq!(percent_encode_path("bcs_grp_abc:cdf28232"), "bcs_grp_abc%3Acdf28232");
    }
    #[test]
    fn slash_encoded() {
        // session_id 理论上不应含 '/'，但若含则编码（防路径穿越/段断裂）。
        assert_eq!(percent_encode_path("a/b"), "a%2Fb");
    }
    #[test]
    fn alphanumeric_dash_tilde_kept() {
        assert_eq!(percent_encode_path("A1-_~"), "A1-_~");
    }
}

fn session_id_from_key(key: &str) -> &str {
    // key = "session-files/{env}/{session_id}/{file_id}/{file_name}"
    let mut it = key.split('/');
    it.next(); // session-files
    it.next(); // env
    it.next().unwrap_or("") // session_id
}

fn pending_handle(transfer_id: String, ty: &str, expires_at: u64) -> serde_json::Value {
    serde_json::to_value(handle::BaasPendingHandle {
        transfer_id,
        transfer_type: ty.into(),
        expires_at,
    })
    .expect("BaasPendingHandle serializable")
}

fn bad(msg: &str) -> StorageError {
    StorageError::Backend(anyhow::anyhow!("baas bad response: {msg}"))
}

/// Parse an HTTP response, assert `code == 0`, return the `data` object.
async fn baas_data(resp: reqwest::Response) -> Result<serde_json::Value, StorageError> {
    let status = resp.status();
    if !status.is_success() {
        let body: serde_json::Value =
            resp.json().await.map_err(|e| StorageError::Backend(e.into()))?;
        let detail = &body["detail"];
        let code = detail["error"].as_str().unwrap_or("");
        let msg = detail["message"].as_str().unwrap_or("");
        // DELETE-idempotent short-circuit handled by callers; here surface the mapped error.
        return Err(crate::error::map_baas_error(code, status.as_u16(), msg));
    }
    let body: serde_json::Value =
        resp.json().await.map_err(|e| StorageError::Backend(e.into()))?;
    if body["code"].as_i64() != Some(0) {
        return Err(bad(&format!("non-zero code: {}", body)));
    }
    Ok(body["data"].clone())
}

/// Idempotent abort helper: 2xx responses and 409 TRANSFER_STATE_CONFLICT
/// (already terminal) are both treated as success. All other errors go
/// through the standard `map_baas_error`.
async fn baas_data_or_conflict_ok(resp: reqwest::Response) -> Result<serde_json::Value, StorageError> {
    let status = resp.status();
    if status.is_success() {
        return Ok(serde_json::Value::Null); // abort idempotent success, no data needed
    }
    let body: serde_json::Value = resp.json().await.map_err(|e| StorageError::Backend(e.into()))?;
    let code = body["detail"]["error"].as_str().unwrap_or("");
    if code == "TRANSFER_STATE_CONFLICT" {
        return Ok(serde_json::Value::Null); // already terminal — idempotent ok
    }
    Err(crate::error::map_baas_error(code, status.as_u16(), body["detail"]["message"].as_str().unwrap_or("")))
}

/// ISO 8601 -> unix seconds via chrono `DateTime::parse_from_rfc3339`.
fn parse_iso_to_unix(s: &str) -> Option<u64> {
    use chrono::DateTime;
    if s.is_empty() { return None; }
    let dt = DateTime::parse_from_rfc3339(s).ok()?;
    Some(dt.timestamp().max(0) as u64)
}

/// Current unix timestamp in seconds.
/// Used as a fallback when baas returns `expires_at: null`.
fn now_unix_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn actor_kind_str(kind: ActorKind) -> &'static str {
    match kind {
        ActorKind::Bot => "bot",
        ActorKind::Human => "human",
    }
}

fn operator_str(caller: Option<&ActorRef>) -> String {
    match caller {
        Some(a) => format!("{}:{}", actor_kind_str(a.actor_kind), a.actor_id),
        None => "bcs".to_string(),
    }
}

#[async_trait]
impl StoragePlugin for BaasStoragePlugin {
    fn backend_name(&self) -> &'static str { "baas" }

    fn capabilities(&self) -> StorageCapabilities { self.caps }

    async fn prepare_upload(&self, req: UploadPrepareRequest, caller: Option<&ActorRef>) -> Result<PreparedUpload, StorageError> {
        let session_id = session_id_from_key(&req.key);
        let base = self.base_for_session(session_id);
        let body = serde_json::json!({
            "filename": req.file_name,
            "file_size": req.size,
            "expire_seconds": req.ttl_secs,
            "staging_subdir": serde_json::Value::Null,
            "operator": operator_str(caller),
        });
        let resp = self
            .auth(self.http.post(format!("{base}/upload-url")).json(&body))
            .send()
            .await
            .map_err(|e| StorageError::Backend(e.into()))?;
        let data = baas_data(resp).await?;
        let transfer_id = data["transfer_id"]
            .as_str()
            .ok_or_else(|| bad("missing transfer_id"))?
            .to_string();
        let type_str = data["type"].as_str().unwrap_or("SINGLE").to_string();
        let expires_at = parse_iso_to_unix(data["expires_at"].as_str().unwrap_or(""))
            .unwrap_or_else(|| now_unix_secs().saturating_add(req.ttl_secs));

        let (client_target, handle) = if type_str == "MULTIPART" {
            let part_size = data["part_size"].as_u64().unwrap_or(0);
            let part_count = data["part_count"].as_u64().unwrap_or(0) as u32;
            let parts: Vec<UploadPartUrl> = data["parts"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|p| UploadPartUrl {
                    part_number: p["part_number"].as_u64().unwrap_or(0) as u16,
                    url: p["upload_url"].as_str().unwrap_or("").to_string(),
                })
                .collect();
            let ct = ClientUploadTarget::Direct {
                mode: UploadMode::Multipart,
                url: None,
                parts: Some(parts),
                part_size: Some(part_size),
                part_count: Some(part_count),
            };
            (ct, pending_handle(transfer_id, "MULTIPART", expires_at))
        } else {
            let url = data["upload_url"].as_str().unwrap_or("").to_string();
            let ct = ClientUploadTarget::Direct {
                mode: UploadMode::Single,
                url: Some(url),
                parts: None,
                part_size: None,
                part_count: None,
            };
            (ct, pending_handle(transfer_id, "SINGLE", expires_at))
        };
        Ok(PreparedUpload {
            handle: UploadHandle {
                backend: "baas".into(),
                key: req.key.clone(),
                backend_handle: handle,
                expires_at,
            },
            client_target,
            expires_at,
        })
    }
    async fn stream_upload(&self, _h: &UploadHandle, _p: Option<u16>, _b: ByteStream) -> Result<(), StorageError> {
        Err(StorageError::Unsupported("baas")) // presign_put backend: never called by BCS
    }
    async fn complete_upload(&self, handle: &UploadHandle) -> Result<StorageObjectMeta, StorageError> {
        let pending: handle::BaasPendingHandle = serde_json::from_value(handle.backend_handle.clone())
            .map_err(|e| StorageError::Backend(e.into()))?;
        let session_id = session_id_from_key(&handle.key);
        let base = self.base_for_session(session_id);
        let resp = self.auth(self.http.post(format!("{base}/upload-url/{}/complete",
                    percent_encode_path(&pending.transfer_id))).json(&serde_json::json!({})))
            .send().await.map_err(|e| StorageError::Backend(e.into()))?;
        let _data = baas_data(resp).await?; // sync DONE; data has status
        Ok(StorageObjectMeta { key: handle.key.clone(), size: 0, sha256: None })
    }

    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError> {
        let pending: handle::BaasPendingHandle = serde_json::from_value(handle.backend_handle.clone())
            .map_err(|e| StorageError::Backend(e.into()))?;
        let session_id = session_id_from_key(&handle.key);
        let base = self.base_for_session(session_id);
        let resp = self.auth(self.http.delete(format!("{base}/upload-url/{}",
                    percent_encode_path(&pending.transfer_id)))).send().await
            .map_err(|e| StorageError::Backend(e.into()))?;
        // CANCELLED (or already terminal) — treat 2xx + TRANSFER_STATE_CONFLICT as Ok.
        let _ = baas_data_or_conflict_ok(resp).await?;
        Ok(())
    }
    async fn get_stream(&self, _h: &StorageHandle) -> Result<ByteStream, StorageError> {
        Err(StorageError::Unsupported("baas")) // presign_download backend: 302 path used instead
    }
    async fn presign_get(&self, handle: &StorageHandle, ttl_secs: u64, caller: Option<&ActorRef>) -> Result<PresignGetTicket, StorageError> {
        let ready: BaasReadyHandle = serde_json::from_value(handle.backend_handle.clone())
            .or_else(|_| serde_json::from_value::<BaasPendingHandle>(handle.backend_handle.clone())
                        .map(|p| BaasReadyHandle { transfer_id: p.transfer_id }))
            .map_err(|e| StorageError::Backend(e.into()))?;
        let session_id = session_id_from_key(&handle.key);
        let base = self.base_for_session(session_id);
        let body = serde_json::json!({ "expire_seconds": ttl_secs, "operator": operator_str(caller) });
        let resp = self.auth(self.http.post(format!("{base}/transfers/{}/share-link",
                    percent_encode_path(&ready.transfer_id))).json(&body)).send().await
            .map_err(|e| StorageError::Backend(e.into()))?;
        let data = baas_data(resp).await?;
        let share_url = data["share_url"].as_str().ok_or_else(|| bad("missing share_url"))?.to_string();
        let expires_at = parse_iso_to_unix(data["expires_at"].as_str().unwrap_or("")).unwrap_or_else(|| now_unix_secs().saturating_add(ttl_secs));
        Ok(PresignGetTicket { download_url: share_url, expires_at })
    }
    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError> {
        let ready: BaasReadyHandle = serde_json::from_value(handle.backend_handle.clone())
            .or_else(|_| serde_json::from_value::<BaasPendingHandle>(handle.backend_handle.clone())
                        .map(|p| BaasReadyHandle { transfer_id: p.transfer_id }))
            .map_err(|e| StorageError::Backend(e.into()))?;
        let session_id = session_id_from_key(&handle.key);
        let root = self.session_path_root(session_id);
        let resp = self.auth(self.http.delete(format!("{root}/transfers/{}",
                    percent_encode_path(&ready.transfer_id)))).send().await
            .map_err(|e| StorageError::Backend(e.into()))?;
        if resp.status().is_success() {
            return Ok(());
        }
        let status = resp.status();
        let body: serde_json::Value = resp.json().await.map_err(|e| StorageError::Backend(e.into()))?;
        let code = body["detail"]["error"].as_str().unwrap_or("");
        if crate::error::is_delete_idempotent_ok(Some(code)) {
            return Ok(());
        }
        Err(crate::error::map_baas_error(code, status.as_u16(), body["detail"]["message"].as_str().unwrap_or("")))
    }

    async fn health_check(&self) -> Result<StorageHealth, StorageError> {
        // Probe endpoint (or health_probe_path) without any real transfer_id.
        // Accept 2xx as ok; 401/404/405 means "reachable". 5xx/conn-error = not ok.
        let url = if self.cfg.health_probe_path.is_empty() {
            self.cfg.endpoint.clone()
        } else {
            format!("{}{}", self.cfg.endpoint.trim_end_matches('/'), self.cfg.health_probe_path)
        };
        let resp = self.auth(self.http.get(&url)).send().await;
        match resp {
            Ok(r) => {
                let s = r.status().as_u16();
                let ok = s < 500; // 2xx/3xx/4xx all imply "service responding"
                Ok(StorageHealth { ok, detail: if ok { None } else { Some(format!("baas health HTTP {s}")) } })
            }
            Err(e) => Ok(StorageHealth { ok: false, detail: Some(format!("baas unreachable: {e}")) }),
        }
    }
}

