//! BCSFuse implementation of the outbound visibility synchronization port.

use std::{path::PathBuf, sync::Arc};

use async_trait::async_trait;
use bcs_fuse_client::{BcsFuseConfig, FuseClient};
use bcs_service_api::{
    ContextBotSummary,
    port::{VisibilitySyncPort, VisibilitySyncRequest},
};

use crate::{build_sync_request, load_bot_context, sync_worker_with_retry};

pub struct FuseVisibilitySyncPort {
    client: Arc<FuseClient>,
    config: BcsFuseConfig,
    bots_base_dir: PathBuf,
}

impl FuseVisibilitySyncPort {
    pub fn new(client: Arc<FuseClient>, config: BcsFuseConfig, bots_base_dir: PathBuf) -> Self {
        Self {
            client,
            config,
            bots_base_dir,
        }
    }
}

#[async_trait]
impl VisibilitySyncPort for FuseVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        let bot_context = match load_bot_context(&self.bots_base_dir, &request.bot_uuid) {
            Ok(context) => context,
            Err(error) => {
                tracing::info!(
                    bot_id = %request.bot_uuid,
                    error = %error,
                    "No local bot context found, syncing with empty context"
                );
                ContextBotSummary {
                    bot_uuid: request.bot_uuid.clone(),
                    name: None,
                    emoji: None,
                    identity: None,
                    soul: None,
                    rules: None,
                    memory: None,
                }
            }
        };
        let bot_name = request.capabilities.name.clone().unwrap_or_default();
        let sync_request = build_sync_request(
            &self.config,
            &request.bot_uuid,
            &bot_name,
            request.capabilities.summary.as_deref(),
            &request.capabilities.domains,
            &request.capabilities.skills,
            &bot_context,
            &request.capabilities.visibility,
        );

        sync_worker_with_retry(&self.client, &request.bot_uuid, &sync_request, &self.config).await;
    }
}
