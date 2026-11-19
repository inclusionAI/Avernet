//! Instance-bound Worker control client. Authentication is supplied by composition;
//! credentials must represent this exact registration lifetime at the receiver.
use async_trait::async_trait;
use bcs_dist_model::{Instance, RequestId};
use bcs_service_api::{
    port::{dist_runtime::DistWorkerPeerPort, repo::distributed::ownership::*},
    types::dist_runtime::RuntimeError,
};
use reqwest::{Client, Url, header::HeaderMap};
use serde::{Serialize, de::DeserializeOwned};
use std::time::Duration;

pub struct HttpWorkerPeer {
    instance: Instance,
    client: Client,
    headers: HeaderMap,
}
#[derive(Serialize)]
struct DoneRequest {
    source: Instance,
    target: ShardClaim,
    request_id: RequestId,
}
fn error(code: &str) -> RuntimeError {
    RuntimeError { code: code.into() }
}
impl HttpWorkerPeer {
    pub fn new(
        instance: Instance,
        headers: HeaderMap,
        timeout: Duration,
    ) -> Result<Self, RuntimeError> {
        if headers.is_empty() || timeout.is_zero() {
            return Err(error("worker_peer_configuration"));
        }
        let client = Client::builder()
            .timeout(timeout)
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| error("worker_peer_configuration"))?;
        Ok(Self {
            instance,
            client,
            headers,
        })
    }
    async fn send<T: Serialize + Sync, R: DeserializeOwned>(
        &self,
        target: &MemberRecord,
        path: &str,
        body: &T,
    ) -> Result<R, RuntimeError> {
        let address =
            Url::parse(&target.control_address).map_err(|_| error("worker_peer_address"))?;
        if !matches!(address.scheme(), "http" | "https")
            || address.host_str().is_none()
            || !address.username().is_empty()
            || address.password().is_some()
            || address.fragment().is_some()
            || address.query().is_some()
        {
            return Err(error("worker_peer_address"));
        }
        let url = address
            .join(path)
            .map_err(|_| error("worker_peer_address"))?;
        let mut response = self
            .client
            .post(url)
            .headers(self.headers.clone())
            .json(body)
            .send()
            .await
            .map_err(|_| error("worker_peer_outcome_unknown"))?;
        if !response.status().is_success() {
            return Err(error("worker_peer_outcome_unknown"));
        }
        let mut bytes = Vec::new();
        while let Some(part) = response
            .chunk()
            .await
            .map_err(|_| error("worker_peer_outcome_unknown"))?
        {
            if bytes.len().saturating_add(part.len()) > 16_384 {
                return Err(error("worker_peer_outcome_unknown"));
            }
            bytes.extend_from_slice(&part);
        }
        serde_json::from_slice(&bytes).map_err(|_| error("worker_peer_outcome_unknown"))
    }
}
#[async_trait]
impl DistWorkerPeerPort for HttpWorkerPeer {
    async fn handoff(
        &self,
        target: &MemberRecord,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        if request.from.owner.instance != self.instance || request.target != target.instance {
            return Err(error("worker_peer_identity"));
        }
        let response = self
            .send(target, "/internal/bcn/worker/handoff", &request)
            .await?;
        if let HandoffOutcome::Accepted(record) = &response
            && (record.claim.owner.instance != target.instance
                || record.claim.shard != request.from.shard
                || Some(record.claim.owner.epoch.0) != request.from.owner.epoch.0.checked_add(1))
        {
            return Err(error("worker_peer_outcome_unknown"));
        }
        Ok(response)
    }
    async fn source_done(
        &self,
        target: &MemberRecord,
        source: Instance,
        claim: ShardClaim,
        id: RequestId,
    ) -> Result<bool, RuntimeError> {
        if source != self.instance || claim.owner.instance != target.instance {
            return Err(error("worker_peer_identity"));
        }
        self.send(
            target,
            "/internal/bcn/worker/source-done",
            &DoneRequest {
                source,
                target: claim,
                request_id: id,
            },
        )
        .await
    }
}
