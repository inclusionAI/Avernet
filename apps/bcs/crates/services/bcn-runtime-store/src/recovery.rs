//! Poison metadata stays readable even when a checkpoint cannot be decoded.
use crate::{RuntimeStore, keys::*, persistence::*};
use bcn_runtime_model::*;
impl RuntimeStore {
    pub(crate) async fn begin_session_recovery(
        &self,
        session: &SessionId,
        fence: &OwnershipFence,
        threshold: u32,
    ) -> RuntimeResult<SessionRecovery> {
        if session_shard(session) != fence.shard || threshold == 0 {
            return Err(invalid("recovery shard/threshold"));
        }
        let key = entity("recovery", &session.value);
        let row = self.row(&session.scope, key.clone()).await?;
        let mut state = row
            .as_ref()
            .map(decode::<SessionRecovery>)
            .transpose()?
            .unwrap_or(SessionRecovery {
                fence: fence.clone(),
                takeovers: 0,
                quarantined: false,
            });
        if state.fence != *fence {
            state.takeovers = state.takeovers.saturating_add(1);
            state.fence = fence.clone();
        }
        state.quarantined |= state.takeovers >= threshold;
        self.fenced_write(
            fence,
            vec![condition(key.clone(), &row)],
            vec![put(key, &state)?],
        )
        .await?;
        Ok(state)
    }
}
