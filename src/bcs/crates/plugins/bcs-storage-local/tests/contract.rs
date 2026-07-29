//! Contract test for the local filesystem `StoragePlugin`.
//! Calls the shared `assert_storage_plugin_conforms` suite from `bcs-storage-api`.

use std::sync::Arc;

use bcs_storage_api::StorageCapabilities;
use bcs_storage_local::{LocalStorageConfig, LocalStoragePlugin};

#[tokio::test]
async fn local_conforms() {
    let dir = tempfile::tempdir().unwrap();
    let caps = StorageCapabilities {
        supports_presign_put: false,
        supports_presign_download: false,
        supports_stream_put: true,
        supports_stream_get: true,
        supports_inline_view: true,
        max_object_size: 1024 * 1024,
    };
    let plugin: Arc<dyn bcs_storage_api::StoragePlugin> = Arc::new(LocalStoragePlugin::new(
        LocalStorageConfig {
            data_dir: dir.path().to_path_buf(),
            max_object_size: 1024 * 1024,
        },
    ));
    bcs_storage_api::contract::assert_storage_plugin_conforms(plugin, caps).await;
}