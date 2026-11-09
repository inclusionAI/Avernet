use async_trait::async_trait;
use bcn_runtime_api::port::repo::OwnershipPort;
use bcn_runtime_model::*;
use crate::{RuntimeStore, keys, persistence::*};
impl RuntimeStore {
    pub(crate) async fn ownership_condition(&self, fence: &OwnershipFence) -> RuntimeResult<Condition> {
        let key = keys::owner(&fence.shard);
        let row = self.row(&fence.shard.scope, key.clone()).await?;
        let actual: Option<OwnershipFence> = row.as_ref().map(decode).transpose()?;
        if actual.as_ref() != Some(fence) { return Err(RuntimeError::OwnershipLost); }
        Ok(condition(key, &row))
    }
}
#[async_trait]
impl OwnershipPort for RuntimeStore {
    async fn claim_shard(&self, shard: ShardId, candidate: NodeIdentity, expected: Option<OwnershipFence>) -> RuntimeResult<OwnershipFence> {
        if candidate.scope != shard.scope || shard.number >= 1024 { return Err(RuntimeError::InvalidInput("shard or owner scope".into())); }
        let key = keys::owner(&shard);
        let row = self.row(&shard.scope, key.clone()).await?;
        let actual: Option<OwnershipFence> = row.as_ref().map(decode).transpose()?;
        if actual != expected { return Err(RuntimeError::OwnershipLost); }
        let epoch = actual.map_or(0, |v| v.epoch).checked_add(1).ok_or_else(|| RuntimeError::Storage("epoch exhausted".into()))?;
        let fence = OwnershipFence { shard, owner: candidate, epoch };
        let result = self.write(&fence.shard.scope, vec![condition(key.clone(), &row)], vec![put(key, &fence)?]).await;
        match result { Err(RuntimeError::Conflict(_)) => Err(RuntimeError::OwnershipLost), Err(e) => Err(e), Ok(_) => Ok(fence) }
    }
    async fn transfer_shard(&self, current: OwnershipFence, next: NodeIdentity) -> RuntimeResult<OwnershipFence> {
        self.claim_shard(current.shard.clone(), next, Some(current)).await
    }
    async fn read_owner(&self, shard: &ShardId) -> RuntimeResult<Option<OwnershipFence>> {
        self.row(&shard.scope, keys::owner(shard)).await?.as_ref().map(decode).transpose()
    }
}
