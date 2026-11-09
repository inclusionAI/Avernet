//! Committed-reference publication and bounded leased command delivery.
use async_trait::async_trait;
use serde::{Serialize, Deserialize};
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::{CommandBus, SessionStore};
use crate::{RuntimeStore, keys::*, persistence::*};
#[derive(Serialize, Deserialize)]
struct Delivery { consumer: Option<NodeIdentity>, until_ms: u64, acked: bool }
#[async_trait]
impl CommandBus for RuntimeStore {
    async fn publish(&self, fence: &OwnershipFence, reference: CommittedEffectRef) -> RuntimeResult<Command> {
        if session_shard(&reference.session_id) != fence.shard || reference.command_id.scope != reference.session_id.scope { return Err(invalid("publication scope/shard")); }
        self.ownership_condition(fence).await?;
        let id = &reference.command_id;
        let delivery_key = entity("delivery", &id.value);
        let rows = self.rows(&id.scope, &[entity("command", &id.value), delivery_key.clone()]).await?;
        let command: Command = decode(rows[0].as_ref().ok_or(RuntimeError::NotFound)?)?;
        if command.session_id != reference.session_id { return Err(invalid("command session")); }
        if rows[1].is_some() {
            self.fenced_write(fence, vec![condition(delivery_key, &rows[1])], vec![]).await?;
            return Ok(command);
        }
        let mut cp = self.load(&reference.session_id).await?.ok_or(RuntimeError::NotFound)?;
        if !cp.pending_effects.contains(&command.effect) { return Err(conflict("effect not pending")); }
        cp.pending_effects.retain(|e| e != &command.effect);
        self.fenced_write(fence, vec![condition(delivery_key.clone(), &rows[1]), Condition { key: entity("checkpoint", &reference.session_id.value), expected: Expected::Revision(cp.revision) }], vec![
            put(entity("checkpoint", &reference.session_id.value), &cp)?,
            put(delivery_key, &Delivery { consumer: None, until_ms: 0, acked: false })?,
            put(entity("command-ready", &id.value), &command)?,
        ]).await?;
        Ok(command)
    }
    async fn receive(&self, scope: &Scope, consumer: &NodeIdentity, kind: CommandConsumerKind, after: Option<String>, limit: u32) -> RuntimeResult<Page<Command>> {
        if &consumer.scope != scope { return Err(invalid("consumer scope")); }
        let page = self.page(scope, "command-ready/", after, limit).await?;
        let commands = page.entries.iter().map(|r| decode::<Command>(&r.entry)).collect::<RuntimeResult<Vec<_>>>()?;
        let keys = commands.iter().map(|c| entity("delivery", &c.command_id.value)).collect::<Vec<_>>();
        let rows = self.rows(scope, &keys).await?;
        let now = self.atomic.server_time_ms().await.map_err(storage)?;
        let mut conditions = vec![]; let mut mutations = vec![]; let mut received = vec![];
        for ((command, key), row) in commands.into_iter().zip(keys).zip(rows) {
            let mut delivery: Delivery = decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
            if !kind.accepts(&command.effect) || delivery.acked || (delivery.consumer.as_ref().is_some_and(|n| n != consumer) && delivery.until_ms > now) { continue; }
            delivery.consumer = Some(consumer.clone()); delivery.until_ms = now.checked_add(3000).ok_or_else(|| invalid("lease overflow"))?;
            conditions.push(condition(key.clone(), &row)); mutations.push(put(key, &delivery)?); received.push(command);
        }
        self.write(scope, conditions, mutations).await?;
        Ok(Page { items: received, next_cursor: cursor(page.next_cursor)? })
    }
    async fn ack(&self, id: &CommandId, consumer: &NodeIdentity) -> RuntimeResult<()> {
        if consumer.scope != id.scope { return Err(invalid("consumer scope")); }
        let key = entity("delivery", &id.value);
        let row = self.row(&id.scope, key.clone()).await?;
        let mut delivery: Delivery = decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        if delivery.consumer.as_ref() != Some(consumer) { return Err(RuntimeError::OwnershipLost); }
        if delivery.acked { return Ok(()); }
        let command_row = self.row(&id.scope, entity("command", &id.value)).await?;
        let command: Command = decode(command_row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        let dispatch_id = match &command.effect {
            EffectIntent::SendAgent { request, .. } => Some(&request.dispatch_id),
            EffectIntent::CancelAgent { dispatch_id, .. } => Some(dispatch_id),
            _ => None,
        };
        if let Some(dispatch_id) = dispatch_id {
            if self.row(&id.scope, entity("dispatch", &dispatch_id.value)).await?.is_none() { return Err(conflict("claim dispatch before delivery ACK")); }
        }
        delivery.acked = true;
        self.write(&id.scope, vec![condition(key.clone(), &row)], vec![put(key, &delivery)?, Mutation::Delete { key: entity("command-ready", &id.value) }]).await?;
        Ok(())
    }
}
