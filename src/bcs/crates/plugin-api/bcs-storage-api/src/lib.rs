//! Pluggable storage backend trait for the BCS session workspace.
//!
//! Mirrors the `DbPlugin` / `CachePlugin` pattern: concrete backends implement
//! `StoragePlugin`; `SessionFileService` depends only on the trait. A
//! `FakeStoragePlugin` (in-memory) lives in this crate for service/HTTP test
//! reuse — covering single + multipart paths.

pub mod contract;
pub mod factory;
pub mod fake;

use async_trait::async_trait;
use bytes::Bytes;
use futures::Stream;
use serde::{Deserialize, Serialize};

pub type ByteStream = Box<dyn ByteStreamTrait + Send + Unpin>;

pub trait ByteStreamTrait: Stream<Item = Result<Bytes, std::io::Error>> + Send + Unpin {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct StorageCapabilities {
    pub supports_presign_put: bool,
    pub supports_presign_download: bool,
    pub supports_stream_put: bool,
    pub supports_stream_get: bool,
    pub max_object_size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UploadPrepareRequest {
    pub key: String,
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,
    pub ttl_secs: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PreparedUpload {
    pub handle: UploadHandle,
    pub client_target: ClientUploadTarget,
    pub expires_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ClientUploadTarget {
    /// presign_put backend: direct backend URL(s); bytes bypass BCS.
    Direct {
        mode: UploadMode,
        url: Option<String>,               // Some for Single
        parts: Option<Vec<UploadPartUrl>>, // Some for Multipart
        part_size: Option<u64>,
        part_count: Option<u32>,
    },
    /// non-presign backend (local): BCS serves its own `PUT .../content` proxy.
    ProxyViaBcs,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum UploadMode {
    Single,
    Multipart,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UploadPartUrl {
    pub part_number: u16,
    pub url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UploadHandle {
    pub backend: String,
    pub key: String,
    pub backend_handle: serde_json::Value,
    pub expires_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StorageHandle {
    pub backend: String,
    pub key: String,
    pub backend_handle: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PresignGetTicket {
    pub download_url: String,
    pub expires_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StorageObjectMeta {
    pub key: String,
    pub size: u64,
    pub sha256: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StorageHealth {
    pub ok: bool,
    pub detail: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum StorageError {
    #[error("invalid input: {0}")]
    InvalidInput(String),
    #[error("object not found")]
    NotFound,
    #[error("state conflict: {0}")]
    Conflict(String),
    #[error("unsupported by backend {0}")]
    Unsupported(&'static str),
    #[error("backend error")]
    Backend(#[from] anyhow::Error),
}

#[async_trait]
pub trait StoragePlugin: Send + Sync + 'static {
    fn backend_name(&self) -> &'static str;
    /// Cheap, sync, no IO. Returns a value precomputed at construction.
    fn capabilities(&self) -> StorageCapabilities;

    async fn prepare_upload(&self, req: UploadPrepareRequest) -> Result<PreparedUpload, StorageError>;
    async fn stream_upload(
        &self,
        handle: &UploadHandle,
        part_number: Option<u16>,
        body: ByteStream,
    ) -> Result<(), StorageError>;
    async fn complete_upload(&self, handle: &UploadHandle) -> Result<StorageObjectMeta, StorageError>;
    async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError>;

    async fn get_stream(&self, handle: &StorageHandle) -> Result<ByteStream, StorageError>;
    async fn presign_get(
        &self,
        handle: &StorageHandle,
        ttl_secs: u64,
    ) -> Result<PresignGetTicket, StorageError>;
    async fn delete(&self, handle: &StorageHandle) -> Result<(), StorageError>;

    async fn health_check(&self) -> Result<StorageHealth, StorageError>;
}

/// Wrap a single `Bytes` chunk as a `ByteStream` for proxy upload ingestion.
pub fn byte_stream_from_bytes(b: Bytes) -> ByteStream {
    struct OneShot(std::vec::IntoIter<Bytes>);
    impl Stream for OneShot {
        type Item = Result<Bytes, std::io::Error>;
        fn poll_next(mut self: std::pin::Pin<&mut Self>, _cx: &mut std::task::Context<'_>)
            -> std::task::Poll<Option<Self::Item>> {
            std::task::Poll::Ready(self.0.next().map(Ok))
        }
    }
    impl ByteStreamTrait for OneShot {}
    Box::new(OneShot(vec![b].into_iter()))
}