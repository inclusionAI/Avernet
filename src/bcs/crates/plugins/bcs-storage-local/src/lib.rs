//! Local filesystem `StoragePlugin` for the BCS session workspace.
//!
//! Writes temp files as `{data_dir}/{key}.{rand}.part` (single) or
//! `{data_dir}/{key}.p{n}.{rand}.part` (multipart). On `complete_upload`,
//! scans the data directory for `{key}.*` files: if any `p{n}` parts
//! exist, sorts by n and concatenates to the final path; otherwise renames
//! the single `.part`.  `abort_upload` scans and deletes `{key}.*` temps.
//! `delete` removes the final path (idempotent on absent).  `get_stream`
//! opens the final path via `ReaderStream`.

use std::path::PathBuf;

use async_trait::async_trait;
use bytes::Bytes;
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use tokio::io::AsyncWriteExt;
use tokio_util::io::ReaderStream;

pub use bcs_storage_api::{
    ByteStream, ByteStreamTrait, ClientUploadTarget, PreparedUpload, PresignGetTicket,
    StorageCapabilities, StorageError, StorageHandle, StorageHealth, StorageObjectMeta,
    StoragePlugin, UploadHandle, UploadPrepareRequest,
};

/// Configuration for the local filesystem storage plugin.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalStorageConfig {
    /// Directory where uploaded files and temp files are stored.
    pub data_dir: PathBuf,
    /// Maximum object size in bytes, precomputed into `capabilities()`.
    pub max_object_size: u64,
}

/// Local filesystem implementation of [`StoragePlugin`].
pub struct LocalStoragePlugin {
    data_dir: PathBuf,
    caps: StorageCapabilities,
}

impl LocalStoragePlugin {
    /// Create a new local storage plugin from the given config.
    pub fn new(cfg: LocalStorageConfig) -> Self {
        let caps = StorageCapabilities {
            supports_presign_put: false,
            supports_presign_download: false,
            supports_stream_put: true,
            supports_stream_get: true,
            max_object_size: cfg.max_object_size,
        };
        Self { data_dir: cfg.data_dir, caps }
    }

    /// Path of the final (committed) object for the given key.
    fn final_path(&self, key: &str) -> PathBuf {
        self.data_dir.join(key)
    }

    /// 8-char random alphanumeric suffix for temp file uniqueness.
    fn rand_suffix() -> String {
        (0..8).map(|_| fastrand::alphanumeric()).collect()
    }

    /// Generate a unique temp path:
    /// - `None`     => `{data_dir}/{key}.{rand}.part`
    /// - `Some(n)`  => `{data_dir}/{key}.p{n}.{rand}.part`
    fn temp_path(&self, key: &str, part: Option<u16>) -> PathBuf {
        match part {
            None => self.data_dir.join(format!("{}.{}.part", key, Self::rand_suffix())),
            Some(n) => self.data_dir.join(format!("{}.p{n}.{}.part", key, Self::rand_suffix())),
        }
    }

    /// Resolve the final (committed) path from a `StorageHandle`.
    /// Tries `backend_handle.final_path` first; falls back to deriving
    /// from the key.  This lets the contract test pass `Value::Null`
    /// while allowing the service layer to embed the path if desired.
    fn resolve_final_path(&self, handle: &StorageHandle) -> PathBuf {
        handle
            .backend_handle
            .get("final_path")
            .and_then(|v| v.as_str())
            .map(PathBuf::from)
            .unwrap_or_else(|| self.final_path(&handle.key))
    }
}

// -- ByteStream helper -------------------------------------------------------

/// Wrap any `Stream<Item = Result<Bytes, io::Error>> + Send + Unpin + 'static`
/// into a [`ByteStream`] trait object.
fn box_stream<S>(s: S) -> ByteStream
where
    S: futures::Stream<Item = Result<Bytes, std::io::Error>> + Send + Unpin + 'static,
{
    struct Wrap<S>(S);
    impl<S> futures::Stream for Wrap<S>
    where
        S: futures::Stream<Item = Result<Bytes, std::io::Error>> + Unpin,
    {
        type Item = Result<Bytes, std::io::Error>;
        fn poll_next(
            mut self: std::pin::Pin<&mut Self>,
            cx: &mut std::task::Context<'_>,
        ) -> std::task::Poll<Option<Self::Item>> {
            std::pin::Pin::new(&mut self.0).poll_next(cx)
        }
    }
    impl<S> ByteStreamTrait for Wrap<S> where
        S: futures::Stream<Item = Result<Bytes, std::io::Error>> + Send + Unpin + 'static
    {
    }
    Box::new(Wrap(s))
}

// -- StoragePlugin impl ------------------------------------------------------

#[async_trait]
impl StoragePlugin for LocalStoragePlugin {
    fn backend_name(&self) -> &'static str {
        "local"
    }

    fn capabilities(&self) -> StorageCapabilities {
        self.caps
    }

    async fn prepare_upload(&self, req: UploadPrepareRequest) -> Result<PreparedUpload, StorageError> {
        let final_path = self.final_path(&req.key);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let expires_at = now.saturating_add(req.ttl_secs);
        let handle = UploadHandle {
            backend: self.backend_name().to_string(),
            key: req.key,
            backend_handle: serde_json::json!({
                "final_path": final_path.to_string_lossy(),
                "size": req.size,
            }),
            expires_at,
        };
        Ok(PreparedUpload {
            handle,
            client_target: ClientUploadTarget::ProxyViaBcs,
            expires_at,
        })
    }

    async fn stream_upload(
        &self,
        handle: &UploadHandle,
        part_number: Option<u16>,
        mut body: ByteStream,
    ) -> Result<(), StorageError> {
        let path = self.temp_path(&handle.key, part_number);
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await.ok();
        }
        let mut f = tokio::fs::File::create(&path)
            .await
            .map_err(|e| StorageError::Backend(e.into()))?;
        let max_size = handle
            .backend_handle
            .get("size")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| StorageError::InvalidInput("missing size in backend_handle".into()))?;
        let mut written: u64 = 0;
        while let Some(chunk) = body.next().await {
            let b = chunk.map_err(|e| StorageError::Backend(e.into()))?;
            if written.saturating_add(b.len() as u64) > max_size {
                return Err(StorageError::InvalidInput("exceeds prepared size".into()));
            }
            written = written.saturating_add(b.len() as u64);
            f.write_all(&b)
                .await
                .map_err(|e| StorageError::Backend(e.into()))?;
        }
        f.sync_all()
            .await
            .map_err(|e| StorageError::Backend(e.into()))?;
        tracing::debug!(
            key = %handle.key,
            part = ?part_number,
            path = %path.display(),
            bytes = written,
            "stream_upload complete"
        );
        Ok(())
    }

    async fn complete_upload(
        &self,
        handle: &UploadHandle,
    ) -> Result<StorageObjectMeta, StorageError> {
        let bh = &handle.backend_handle;
        let size = bh
            .get("size")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| StorageError::InvalidInput("missing size".into()))?;
        let final_path = self.final_path(&handle.key);

        let key_leaf = std::path::Path::new(&handle.key)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(&handle.key);
        let prefix = format!("{key_leaf}.", key_leaf = key_leaf);
        let p_prefix = format!("{key_leaf}.p", key_leaf = key_leaf);

        // Collect files matching {key_leaf}.* in the key's parent directory.
        let mut entries: Vec<tokio::fs::DirEntry> = Vec::new();
        let scan_dir = final_path.parent().unwrap_or(&self.data_dir);
        // `prepare_upload` does not create the key's directory — only
        // `stream_upload` does — so a missing scan dir means nothing was ever
        // staged: completing is a state conflict (no parts), not a backend error.
        let mut dir = match tokio::fs::read_dir(scan_dir).await {
            Ok(d) => d,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                return Err(StorageError::Conflict("no staged parts to complete".into()))
            }
            Err(e) => return Err(StorageError::Backend(e.into())),
        };
        while let Some(entry) = dir
            .next_entry()
            .await
            .map_err(|e| StorageError::Backend(e.into()))?
        {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if name_str.starts_with(&prefix) {
                entries.push(entry);
            }
        }

        let parse_part_number = |name: &str| -> Option<u16> {
            let rest = name.strip_prefix(&p_prefix)?;
            let (part_number, suffix) = rest.split_once('.')?;
            let random_suffix = suffix.strip_suffix(".part")?;
            if random_suffix.len() != 8
                || !random_suffix.chars().all(|c| c.is_ascii_alphanumeric())
            {
                return None;
            }
            part_number.parse::<u16>().ok()
        };
        let is_single_temp = |name: &str| {
            name.strip_prefix(&prefix)
                .and_then(|suffix| suffix.strip_suffix(".part"))
                .is_some_and(|random_suffix| {
                    random_suffix.len() == 8
                        && random_suffix.chars().all(|c| c.is_ascii_alphanumeric())
                })
        };

        // Multipart temp files must match {key}.p{n}.{rand}.part exactly.
        let mut parts: Vec<(u16, PathBuf)> = entries
            .iter()
            .filter_map(|entry| {
                let name = entry.file_name();
                parse_part_number(&name.to_string_lossy()).map(|n| (n, entry.path()))
            })
            .collect();

        if !parts.is_empty() {
            // Multipart: sort by part number and concatenate.
            parts.sort_by_key(|(n, _)| *n);

            // Verify sequential part numbers (1-based, contiguous).
            for (i, (n, _)) in parts.iter().enumerate() {
                let expected = (i as u16) + 1;
                if *n != expected {
                    return Err(StorageError::Conflict(format!(
                        "missing part {expected}"
                    )));
                }
            }

            let mut out = tokio::fs::File::create(&final_path)
                .await
                .map_err(|e| StorageError::Backend(e.into()))?;
            let mut total: u64 = 0;
            for (_, tmp) in &parts {
                let mut r = tokio::fs::File::open(tmp)
                    .await
                    .map_err(|e| {
                        if e.kind() == std::io::ErrorKind::NotFound {
                            StorageError::Conflict("missing part file".into())
                        } else {
                            StorageError::Backend(e.into())
                        }
                    })?;
                total = total.saturating_add(
                    tokio::io::copy(&mut r, &mut out)
                        .await
                        .map_err(|e| StorageError::Backend(e.into()))?,
                );
            }
            out.sync_all()
                .await
                .map_err(|e| StorageError::Backend(e.into()))?;

            if total != size {
                let _ = tokio::fs::remove_file(&final_path).await;
                return Err(StorageError::Conflict(format!(
                    "size mismatch {total} != {size}"
                )));
            }

            // Clean up temp parts.
            for (_, tmp) in &parts {
                let _ = tokio::fs::remove_file(tmp).await;
            }

            Ok(StorageObjectMeta { key: handle.key.clone(), size: total, sha256: None })
        } else {
            // Single: expect exactly one temp file.
            let part_files: Vec<_> = entries
                .iter()
                .filter(|e| {
                    let name = e.file_name();
                    is_single_temp(&name.to_string_lossy())
                })
                .collect();

            if part_files.len() != 1 {
                return Err(StorageError::Conflict(format!(
                    "expected 1 part file, found {}",
                    part_files.len()
                )));
            }

            let tmp = part_files[0].path();
            let actual = tokio::fs::metadata(&tmp)
                .await
                .map_err(|e| StorageError::Backend(e.into()))?
                .len();
            if actual != size {
                return Err(StorageError::Conflict(format!(
                    "size mismatch {actual} != {size}"
                )));
            }
            tokio::fs::rename(&tmp, &final_path)
                .await
                .map_err(|e| StorageError::Backend(e.into()))?;
            Ok(StorageObjectMeta { key: handle.key.clone(), size: actual, sha256: None })
        }
    }

    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError> {
        let key_leaf = std::path::Path::new(&handle.key)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(&handle.key);
        let prefix = format!("{key_leaf}.", key_leaf = key_leaf);
        let final_path = self.final_path(&handle.key);
        let scan_dir = final_path.parent().unwrap_or(&self.data_dir);
        // `prepare_upload` does not create the key's directory — only
        // `stream_upload` does — so a missing scan dir means nothing was ever
        // staged: there is nothing to abort. Idempotent Ok, NOT a backend error
        // (this is what made DELETE of a prepared-but-never-uploaded file
        // return 502 STORAGE_BACKEND).
        let mut dir = match tokio::fs::read_dir(scan_dir).await {
            Ok(d) => d,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(StorageError::Backend(e.into())),
        };
        while let Some(entry) = dir
            .next_entry()
            .await
            .map_err(|e| StorageError::Backend(e.into()))?
        {
            let name = entry.file_name();
            if name.to_string_lossy().starts_with(&prefix) {
                let _ = tokio::fs::remove_file(entry.path()).await;
            }
        }
        Ok(())
    }

    async fn get_stream(
        &self,
        handle: &StorageHandle,
    ) -> Result<ByteStream, StorageError> {
        let path = self.resolve_final_path(handle);
        let file = tokio::fs::File::open(&path).await.map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                StorageError::NotFound
            } else {
                StorageError::Backend(e.into())
            }
        })?;
        Ok(box_stream(ReaderStream::new(file)))
    }

    async fn presign_get(
        &self,
        _handle: &StorageHandle,
        _ttl_secs: u64,
    ) -> Result<PresignGetTicket, StorageError> {
        Err(StorageError::Unsupported("local"))
    }

    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError> {
        let path = self.resolve_final_path(handle);
        match tokio::fs::remove_file(&path).await {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(StorageError::Backend(e.into())),
        }
    }

    async fn health_check(&self) -> Result<StorageHealth, StorageError> {
        let probe = self.data_dir.join(".health_check_probe");
        match tokio::fs::write(&probe, b"ok").await {
            Ok(()) => {
                let _ = tokio::fs::remove_file(&probe).await;
                Ok(StorageHealth { ok: true, detail: None })
            }
            Err(e) => Ok(StorageHealth {
                ok: false,
                detail: Some(e.to_string()),
            }),
        }
    }
}

// -- Unit tests --------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use bytes::Bytes;

    fn plugin() -> (LocalStoragePlugin, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let p = LocalStoragePlugin::new(LocalStorageConfig {
            data_dir: dir.path().to_path_buf(),
            max_object_size: 1024 * 1024,
        });
        (p, dir)
    }

    fn req(key: &str, size: u64) -> UploadPrepareRequest {
        UploadPrepareRequest {
            key: key.to_string(),
            file_name: "f".into(),
            mime_type: "application/octet-stream".into(),
            size,
            ttl_secs: 300,
        }
    }

    fn stream_of(b: Bytes) -> ByteStream {
        bcs_storage_api::byte_stream_from_bytes(b)
    }

    #[tokio::test]
    async fn multipart_roundtrip() {
        let (p, _dir) = plugin();
        let key = "multipart";
        let prep = p.prepare_upload(req(key, 6)).await.unwrap();
        p.stream_upload(&prep.handle, Some(1), stream_of(Bytes::from_static(b"abc")))
            .await
            .unwrap();
        p.stream_upload(&prep.handle, Some(2), stream_of(Bytes::from_static(b"def")))
            .await
            .unwrap();

        let meta = p.complete_upload(&prep.handle).await.unwrap();
        assert_eq!(meta.size, 6);

        let handle = StorageHandle {
            backend: "local".into(),
            key: key.to_string(),
            backend_handle: serde_json::Value::Null,
        };
        let mut stream = p.get_stream(&handle).await.unwrap();
        let chunks: Vec<Result<Bytes, std::io::Error>> =
            StreamExt::collect(stream.as_mut()).await;
        let assembled: Vec<u8> = chunks
            .into_iter()
            .flat_map(|chunk| chunk.unwrap())
            .collect();
        assert_eq!(assembled, b"abcdef");
    }

    #[tokio::test]
    async fn multipart_missing_part_yields_conflict() {
        let (p, _dir) = plugin();
        let prep = p.prepare_upload(req("k1", 6)).await.unwrap();
        // Upload part 1 only — part 2 never arrives.
        p.stream_upload(&prep.handle, Some(1), stream_of(Bytes::from_static(b"abc")))
            .await
            .unwrap();
        let err = p.complete_upload(&prep.handle).await.unwrap_err();
        assert!(matches!(err, StorageError::Conflict(_)));
    }

    #[tokio::test]
    async fn abort_removes_temp_files() {
        let (p, _dir) = plugin();
        let prep = p.prepare_upload(req("k2", 3)).await.unwrap();
        p.stream_upload(&prep.handle, None, stream_of(Bytes::from_static(b"abc")))
            .await
            .unwrap();
        p.abort_upload(&prep.handle).await.unwrap();
        // After abort, complete must fail — no temp files left.
        let err = p.complete_upload(&prep.handle).await.unwrap_err();
        assert!(matches!(err, StorageError::Conflict(_)));
    }

    #[tokio::test]
    async fn presign_get_returns_unsupported() {
        let (p, _dir) = plugin();
        let h = StorageHandle {
            backend: "local".into(),
            key: "k3".into(),
            backend_handle: serde_json::Value::Null,
        };
        let err = p.presign_get(&h, 300).await.unwrap_err();
        assert!(matches!(err, StorageError::Unsupported("local")));
    }

    #[tokio::test]
    async fn delete_absent_object_ok() {
        let (p, _dir) = plugin();
        let h = StorageHandle {
            backend: "local".into(),
            key: "nonexistent".into(),
            backend_handle: serde_json::Value::Null,
        };
        assert!(p.delete(&h).await.is_ok());
    }

    #[tokio::test]
    async fn abort_prepared_never_uploaded_is_ok() {
        // Regression: `prepare_upload` does not create the key's directory
        // (only `stream_upload` does). Aborting a prepared-but-never-uploaded
        // file therefore scans a non-existent directory — that must be an
        // idempotent Ok, NOT a `Backend` (502) error. Uses a slashed key so the
        // scan dir is a nested path that was never created.
        let (p, _dir) = plugin();
        let prepared = p
            .prepare_upload(req("session-files/test/sid/fid/free_chat.png", 100))
            .await
            .unwrap();
        assert!(p.abort_upload(&prepared.handle).await.is_ok());
        // Idempotent: a second abort is still Ok.
        assert!(p.abort_upload(&prepared.handle).await.is_ok());
    }

    #[tokio::test]
    async fn complete_prepared_never_uploaded_is_conflict() {
        // Same never-staged condition, but on `complete_upload`: a missing scan
        // dir is a state conflict (no parts), not a backend error.
        let (p, _dir) = plugin();
        let prepared = p
            .prepare_upload(req("session-files/test/sid/fid/no-parts.bin", 100))
            .await
            .unwrap();
        let err = p.complete_upload(&prepared.handle).await.unwrap_err();
        assert!(matches!(err, StorageError::Conflict(_)));
    }

    #[test]
    fn capabilities_is_sync_and_cheap() {
        let (p, _dir) = plugin();
        let c = p.capabilities();
        assert!(!c.supports_presign_put);
        assert!(!c.supports_presign_download);
        assert!(c.supports_stream_put);
        assert!(c.supports_stream_get);
        assert_eq!(c.max_object_size, 1024 * 1024);
    }

    #[tokio::test]
    async fn health_check_probes_writable() {
        let (p, _dir) = plugin();
        let h = p.health_check().await.unwrap();
        assert!(h.ok);
        assert!(h.detail.is_none());
    }

    #[tokio::test]
    async fn slashed_key_roundtrip() {
        let (p, _dir) = plugin();
        let key = "session-files/test/sid/fid/file.txt";
        let body = Bytes::from_static(b"hello slashed key");
        let size = body.len() as u64;

        let prep = p.prepare_upload(req(key, size)).await.unwrap();
        p.stream_upload(&prep.handle, None, stream_of(body.clone()))
            .await
            .unwrap();
        let meta = p.complete_upload(&prep.handle).await.unwrap();
        assert_eq!(meta.key, key);
        assert_eq!(meta.size, size);

        // Read back via get_stream using a handle with the final_path.
        let h = StorageHandle {
            backend: "local".into(),
            key: key.to_string(),
            backend_handle: serde_json::json!({ "final_path": p.final_path(key).to_string_lossy() }),
        };
        let mut stream = p.get_stream(&h).await.unwrap();
        let chunks: Vec<Result<Bytes, std::io::Error>> =
            StreamExt::collect(stream.as_mut()).await;
        let mut assembled = Vec::new();
        for c in chunks {
            assembled.extend_from_slice(&c.unwrap());
        }
        assert_eq!(assembled, body);
    }

    #[tokio::test]
    async fn single_upload_with_p_prefixed_suffix_roundtrips() {
        let (p, _dir) = plugin();
        let key = "session-files/test/sid/fid/file.txt";
        let body = Bytes::from_static(b"single upload with p-prefixed suffix");
        let size = body.len() as u64;
        let prep = p.prepare_upload(req(key, size)).await.unwrap();

        let staged = p.data_dir.join(format!("{key}.pABCDEFG.part"));
        tokio::fs::create_dir_all(staged.parent().unwrap())
            .await
            .unwrap();
        tokio::fs::write(&staged, &body).await.unwrap();

        let meta = p.complete_upload(&prep.handle).await.unwrap();
        assert_eq!(meta.key, key);
        assert_eq!(meta.size, size);

        let handle = StorageHandle {
            backend: "local".into(),
            key: key.to_string(),
            backend_handle: serde_json::json!({ "final_path": p.final_path(key).to_string_lossy() }),
        };
        let mut stream = p.get_stream(&handle).await.unwrap();
        let chunks: Vec<Result<Bytes, std::io::Error>> =
            StreamExt::collect(stream.as_mut()).await;
        let assembled: Vec<u8> = chunks
            .into_iter()
            .flat_map(|chunk| chunk.unwrap())
            .collect();
        assert_eq!(assembled, body);
    }

    #[tokio::test]
    async fn malformed_p_prefixed_temp_is_rejected() {
        let (p, _dir) = plugin();
        let key = "session-files/test/sid/fid/file.txt";
        let prep = p.prepare_upload(req(key, 3)).await.unwrap();
        let staged = p.data_dir.join(format!("{key}.pBAD.X.part"));
        tokio::fs::create_dir_all(staged.parent().unwrap())
            .await
            .unwrap();
        tokio::fs::write(&staged, b"abc").await.unwrap();

        let err = p.complete_upload(&prep.handle).await.unwrap_err();
        assert!(matches!(err, StorageError::Conflict(_)));
    }

    #[tokio::test]
    async fn stream_upload_rejects_oversize() {
        let (p, _dir) = plugin();
        // Prepare size 5, stream 6 bytes.
        let prep = p.prepare_upload(req("k-oversize", 5)).await.unwrap();
        let err = p
            .stream_upload(
                &prep.handle,
                None,
                stream_of(Bytes::from_static(b"123456")),
            )
            .await
            .unwrap_err();
        assert!(matches!(err, StorageError::InvalidInput(_)));
    }
}
