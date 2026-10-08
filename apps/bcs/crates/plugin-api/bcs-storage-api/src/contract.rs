//! Shared contract suite for any `StoragePlugin`. Each backend crate calls
//! `assert_storage_plugin_conforms` from its own integration test.

use std::sync::Arc;

use bytes::Bytes;
use futures::StreamExt;

use crate::{
    ByteStream, ByteStreamTrait, ClientUploadTarget, StorageCapabilities, StorageError,
    StorageHandle, StoragePlugin, UploadPrepareRequest,
};

struct VecStream(std::vec::IntoIter<Bytes>);
impl futures::Stream for VecStream {
    type Item = Result<Bytes, std::io::Error>;
    fn poll_next(mut self: std::pin::Pin<&mut Self>, _cx: &mut std::task::Context<'_>)
        -> std::task::Poll<Option<Self::Item>> {
        std::task::Poll::Ready(self.0.next().map(Ok))
    }
}
impl ByteStreamTrait for VecStream {}
fn stream_of(b: Bytes) -> ByteStream { Box::new(VecStream(vec![b].into_iter())) }

pub async fn assert_storage_plugin_conforms(plugin: Arc<dyn StoragePlugin>, expected_caps: StorageCapabilities) {
    assert_eq!(plugin.capabilities(), expected_caps);
    assert!(!plugin.backend_name().is_empty());

        let key = format!("contract-{}", line!());
        let req = UploadPrepareRequest {
            key: key.clone(), file_name: "f".into(), mime_type: "application/octet-stream".into(),
            size: 5, ttl_secs: 300,
        };
        let prep = plugin.prepare_upload(req, None).await.unwrap();

    let payload = Bytes::from_static(b"hello");
    match &prep.client_target {
            ClientUploadTarget::ProxyViaBcs => {
                plugin.stream_upload(&prep.handle, None, stream_of(payload.clone())).await.unwrap();
            }
        ClientUploadTarget::Direct { .. } => {
            // presign_put backend: bytes bypass BCS; emulate by staging directly.
            plugin.stream_upload(&prep.handle, None, stream_of(payload.clone())).await.unwrap();
        }
    }
    let meta = plugin.complete_upload(&prep.handle).await.unwrap();
    assert_eq!(meta.size, payload.len() as u64);

    let h = StorageHandle { backend: prep.handle.backend.clone(), key: key.clone(), backend_handle: serde_json::Value::Null };
    let mut s = plugin.get_stream(&h).await.unwrap();
    let mut got = Vec::new();
    while let Some(c) = s.next().await { got.extend_from_slice(&c.unwrap()); }
    assert_eq!(got, payload.as_ref());

    // delete idempotent + makes NotFound
    plugin.delete(&h).await.unwrap();
    plugin.delete(&h).await.unwrap();
    assert!(matches!(plugin.get_stream(&h).await, Err(StorageError::NotFound)));
}