//! In-memory `StoragePlugin` for tests. Covers single + multipart paths.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bytes::Bytes;
use futures::StreamExt;

use crate::{
    ByteStream, ClientUploadTarget, PreparedUpload, PresignGetTicket,
    StorageCapabilities, StorageError, StorageHandle, StorageHealth, StorageObjectMeta,
    StoragePlugin, UploadHandle, UploadPrepareRequest,
};

#[derive(Clone, Default)]
pub struct FakeStoragePlugin {
    caps: StorageCapabilities,
    objects: Arc<Mutex<HashMap<String, Bytes>>>, // key -> final bytes
    staging: Arc<Mutex<HashMap<String, HashMap<Option<u16>, Bytes>>>>, // key -> {part_number: bytes}
}

impl FakeStoragePlugin {
    pub fn new(caps: StorageCapabilities) -> Self {
        Self { caps, ..Default::default() }
    }
}

fn make_stream(b: Bytes) -> ByteStream {
    crate::byte_stream_from_bytes(b)
}

#[async_trait]
impl StoragePlugin for FakeStoragePlugin {
    fn backend_name(&self) -> &'static str { "fake" }
    fn capabilities(&self) -> StorageCapabilities { self.caps }

    async fn prepare_upload(&self, req: UploadPrepareRequest, _caller: Option<&crate::ActorRef>) -> Result<PreparedUpload, StorageError> {
        let handle = UploadHandle {
            backend: "fake".into(),
            key: req.key.clone(),
            backend_handle: serde_json::json!({ "size": req.size }),
            expires_at: req.ttl_secs,
        };
        // Fake is always a proxy backend (stream_upload receives bytes),
        // regardless of caps — keeps the fake usable for both routing paths.
        Ok(PreparedUpload {
            handle,
            client_target: ClientUploadTarget::ProxyViaBcs,
            expires_at: req.ttl_secs,
        })
    }

    async fn stream_upload(
        &self,
        handle: &UploadHandle,
        part_number: Option<u16>,
        mut body: ByteStream,
    ) -> Result<(), StorageError> {
        let mut buf = Vec::new();
        while let Some(chunk) = body.next().await {
            buf.extend_from_slice(&chunk.map_err(|e| StorageError::Backend(e.into()))?);
        }
        self.staging
            .lock().unwrap()
            .entry(handle.key.clone())
            .or_default()
            .insert(part_number, Bytes::from(buf));
        Ok(())
    }

    async fn complete_upload(&self, handle: &UploadHandle) -> Result<StorageObjectMeta, StorageError> {
        let mut parts = self.staging.lock().unwrap().remove(&handle.key)
            .ok_or_else(|| StorageError::Conflict("no staged bytes".into()))?;
        let size: u64 = parts.values().map(|b| b.len() as u64).sum();
        let mut combined = Vec::with_capacity(size as usize);
        let mut keys: Vec<Option<u16>> = parts.keys().cloned().collect();
        keys.sort();
        for k in keys { combined.extend_from_slice(&parts.remove(&k).unwrap()); }
        let bytes = Bytes::from(combined);
        self.objects.lock().unwrap().insert(handle.key.clone(), bytes.clone());
        Ok(StorageObjectMeta { key: handle.key.clone(), size, sha256: None })
    }

    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError> {
        self.staging.lock().unwrap().remove(&handle.key);
        self.objects.lock().unwrap().remove(&handle.key);
        Ok(())
    }

    async fn get_stream(&self, handle: &StorageHandle) -> Result<ByteStream, StorageError> {
        let bytes = self.objects.lock().unwrap().get(&handle.key).cloned()
            .ok_or(StorageError::NotFound)?;
        Ok(make_stream(bytes))
    }

    async fn presign_get(&self, handle: &StorageHandle, ttl_secs: u64, _caller: Option<&crate::ActorRef>) -> Result<PresignGetTicket, StorageError> {
        Ok(PresignGetTicket {
            download_url: format!("fake://{}", handle.key),
            expires_at: ttl_secs,
        })
    }

    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError> {
        self.objects.lock().unwrap().remove(&handle.key); // idempotent
        Ok(())
    }

    async fn health_check(&self) -> Result<StorageHealth, StorageError> {
        Ok(StorageHealth { ok: true, detail: None })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::UploadPrepareRequest;

    fn caps() -> StorageCapabilities {
        StorageCapabilities {
            supports_presign_put: false, supports_presign_download: false,
            supports_stream_put: true, supports_stream_get: true,
            max_object_size: 1024 * 1024 * 1024,
        }
    }
    fn req(key: &str, size: u64) -> UploadPrepareRequest {
        UploadPrepareRequest { key: key.to_string(), file_name: "f".into(), mime_type: "application/octet-stream".into(), size, ttl_secs: 300 }
    }

    #[tokio::test]
    async fn single_roundtrip() {
        let p = FakeStoragePlugin::new(caps());
        let prep = p.prepare_upload(req("k1", 3), None).await.unwrap();
        let payload = Bytes::from_static(b"abc");
        p.stream_upload(&prep.handle, None, make_stream(payload.clone())).await.unwrap();
        let meta = p.complete_upload(&prep.handle).await.unwrap();
        assert_eq!(meta.size, 3);
        let h = StorageHandle { backend: "fake".into(), key: "k1".into(), backend_handle: serde_json::Value::Null };
        let mut s = p.get_stream(&h).await.unwrap();
        let mut got = Vec::new();
        while let Some(c) = s.next().await { got.extend_from_slice(&c.unwrap()); }
        assert_eq!(got, payload.as_ref());
    }

    #[tokio::test]
    async fn multipart_roundtrip() {
        let p = FakeStoragePlugin::new(caps());
        let prep = p.prepare_upload(req("k2", 6), None).await.unwrap();
        p.stream_upload(&prep.handle, Some(1), make_stream(Bytes::from_static(b"aaa"))).await.unwrap();
        p.stream_upload(&prep.handle, Some(2), make_stream(Bytes::from_static(b"bbb"))).await.unwrap();
        let meta = p.complete_upload(&prep.handle).await.unwrap();
        assert_eq!(meta.size, 6);
        let h = StorageHandle { backend: "fake".into(), key: "k2".into(), backend_handle: serde_json::Value::Null };
        let mut s = p.get_stream(&h).await.unwrap();
        let mut got = Vec::new();
        while let Some(c) = s.next().await { got.extend_from_slice(&c.unwrap()); }
        assert_eq!(got, b"aaabbb");
    }

    #[tokio::test]
    async fn abort_makes_object_not_found() {
        let p = FakeStoragePlugin::new(caps());
        let prep = p.prepare_upload(req("k3", 3), None).await.unwrap();
        p.stream_upload(&prep.handle, None, make_stream(Bytes::from_static(b"abc"))).await.unwrap();
        p.abort_upload(&prep.handle).await.unwrap();
        let h = StorageHandle { backend: "fake".into(), key: "k3".into(), backend_handle: serde_json::Value::Null };
        assert!(matches!(p.get_stream(&h).await, Err(StorageError::NotFound)));
    }

    #[tokio::test]
    async fn delete_is_idempotent_and_makes_not_found() {
        let p = FakeStoragePlugin::new(caps());
        let prep = p.prepare_upload(req("k4", 3), None).await.unwrap();
        p.stream_upload(&prep.handle, None, make_stream(Bytes::from_static(b"abc"))).await.unwrap();
        p.complete_upload(&prep.handle).await.unwrap();
        let h = StorageHandle { backend: "fake".into(), key: "k4".into(), backend_handle: serde_json::Value::Null };
        p.delete(&h).await.unwrap();
        p.delete(&h).await.unwrap(); // idempotent Ok
        assert!(matches!(p.get_stream(&h).await, Err(StorageError::NotFound)));
    }

    #[tokio::test]
    async fn delete_missing_object_ok() {
        let p = FakeStoragePlugin::new(caps());
        let h = StorageHandle { backend: "fake".into(), key: "never".into(), backend_handle: serde_json::Value::Null };
        assert!(p.delete(&h).await.is_ok());
    }

    #[test]
    fn capabilities_is_sync_and_cheap() {
        let p = FakeStoragePlugin::new(caps());
        // Calling capabilities() off an async context proves no IO is needed.
        let c = p.capabilities();
        assert_eq!(c, caps());
    }
}