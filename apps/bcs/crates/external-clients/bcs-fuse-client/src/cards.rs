//! Transport contract for the Fuse v2 Card catalog.

use std::collections::BTreeMap;

use bcs_network_tracing::RequestBuilderExt;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::{FuseClient, FuseClientError};

#[derive(Debug, Clone, Serialize)]
pub struct CardWriteRequest {
    pub resource_type: CardResourceType,
    pub labels: BTreeMap<String, Vec<String>>,
    pub card: Value,
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum CardResourceType {
    Agent,
    Group,
}

#[derive(Debug, Deserialize)]
pub struct CardWriteResponse {
    pub saved: bool,
    pub card_version: u64,
}

#[derive(Debug, Serialize)]
pub struct CardSearchRequest {
    pub query: String,
    pub limit: u32,
    pub filters: BTreeMap<String, Vec<String>>,
}

#[derive(Debug, Deserialize)]
pub struct CardSearchItem {
    pub id: String,
    pub name: String,
    pub summary: String,
    pub score: f64,
    pub score_source: String,
}

#[derive(Debug, Deserialize)]
pub struct CardSearchResponse {
    pub items: Vec<CardSearchItem>,
    pub status: String,
    pub reasons: Vec<String>,
}

impl FuseClient {
    fn card_url(&self, id: &str) -> Result<reqwest::Url, FuseClientError> {
        if id.is_empty() || id.chars().count() > 128 || id.chars().any(char::is_whitespace) {
            return Err(FuseClientError::HttpError("invalid Card id".into()));
        }
        let mut url = reqwest::Url::parse(&format!(
            "{}/api/v2/agent-cards", self.base_url.trim_end_matches('/')
        )).map_err(|error| FuseClientError::HttpError(error.to_string()))?;
        url.path_segments_mut()
            .map_err(|_| FuseClientError::HttpError("invalid Fuse base URL".into()))?
            .push(id);
        Ok(url)
    }

    pub async fn put_card(
        &self, id: &str, request: &CardWriteRequest,
    ) -> Result<CardWriteResponse, FuseClientError> {
        let url = self.card_url(id)?;
        let response = self.sync_client.request(|http| http.put(url).json(request))
            .send_traced().await?.error_for_status()?.json::<CardWriteResponse>().await?;
        if !response.saved {
            return Err(FuseClientError::InvalidResponse("Card was not saved".into()));
        }
        Ok(response)
    }

    pub async fn delete_card(&self, id: &str) -> Result<(), FuseClientError> {
        #[derive(Deserialize)]
        struct Deleted { deleted: bool }
        // Fuse returns 202 even for absent Cards; a route-level 404 is an error.
        let url = self.card_url(id)?;
        let response = self.sync_client.request(|http| http.delete(url))
            .send_traced().await?.error_for_status()?.json::<Deleted>().await?;
        if !response.deleted {
            return Err(FuseClientError::InvalidResponse("Card was not deleted".into()));
        }
        Ok(())
    }

    pub async fn search_cards(
        &self, request: &CardSearchRequest,
    ) -> Result<CardSearchResponse, FuseClientError> {
        self.sync_client.request(|http| http.post(format!("{}/api/v2/search", self.base_url.trim_end_matches('/'))).json(request))
            .send_traced().await?.error_for_status()?
            .json().await.map_err(Into::into)
    }
}
