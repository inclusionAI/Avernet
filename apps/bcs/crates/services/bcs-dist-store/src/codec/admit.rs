use super::invalid;
use bcs_dist_model::{Digest, Sid, Tid};
use bcs_service_api::port::repo::distributed::common::StoreError;
use serde::{Deserialize, Serialize};

/// Value of `…:admit:<input_id>`: the digest an input was admitted with and
/// where it went. Same digest on a retry = accepted; different = conflict.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AdmitRecord {
    pub digest: Digest,
    pub sid: Sid,
    pub tid: Tid,
}

impl AdmitRecord {
    pub fn encode(&self) -> Result<Vec<u8>, StoreError> {
        serde_json::to_vec(self).map_err(|_| invalid("admit_encode"))
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, StoreError> {
        serde_json::from_slice(bytes).map_err(|_| invalid("admit_decode"))
    }
}
