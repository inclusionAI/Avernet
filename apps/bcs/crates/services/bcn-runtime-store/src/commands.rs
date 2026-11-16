//! Committed-reference publication and bounded leased command delivery.
use async_trait::async_trait;
use serde::{Serialize, Deserialize};
use bcn_runtime_model::*;
use bcn_runtime_api::port::repo::{CommandBus, SessionStore};
use crate::{RuntimeStore, keys::*, persistence::*};
#[derive(Serialize, Deserialize)]
pub(crate) struct Delivery {
    consumer: Option<NodeIdentity>, until_ms: u64, acked: bool,
    #[serde(default)] epoch: u64,
    #[serde(default)] completed_event: Option<EventId>,
}
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
            put(delivery_key, &Delivery { consumer: None, until_ms: 0, acked: false, epoch: 0, completed_event: None })?,
            put(entity("command-ready", &id.value), &command)?,
        ]).await?;
        Ok(command)
    }
    async fn receive(&self, scope: &Scope, consumer: &NodeIdentity, kind: CommandConsumerKind, after: Option<String>, limit: u32) -> RuntimeResult<Page<Command>> {
        let page=self.receive_commands(scope,consumer,kind,after,limit).await?;
        Ok(Page {items:page.items.into_iter().map(|leased|leased.command).collect(),next_cursor:page.next_cursor})
    }
    async fn receive_effects(&self, scope:&Scope, consumer:&NodeIdentity, after:Option<String>, limit:u32)->RuntimeResult<Page<LeasedCommand>> {
        self.receive_commands(scope,consumer,CommandConsumerKind::Worker,after,limit).await
    }
    async fn renew_effect(&self, fence:&CommandDeliveryFence)->RuntimeResult<u64> {
        let (key,row,mut delivery,command)=self.effect_delivery(fence).await?;
        if !CommandConsumerKind::Worker.accepts(&command.effect) || delivery.acked {return Err(RuntimeError::OwnershipLost)}
        let now=self.atomic.server_time_ms().await.map_err(storage)?;
        if delivery.until_ms<=now {return Err(RuntimeError::OwnershipLost)}
        delivery.until_ms=now.checked_add(3000).ok_or_else(||invalid("lease overflow"))?;
        self.write(&fence.command_id.scope,vec![condition(key.clone(),&row)],vec![put(key,&delivery)?]).await?;
        Ok(3000)
    }
    async fn complete_effect(&self, fence:&CommandDeliveryFence, input:SessionInput)->RuntimeResult<u64> {
        let (key,row,mut delivery,command)=self.effect_delivery(fence).await?;
        match (&command.effect,&input.kind) {
            (EffectIntent::Judge{request},SessionInputKind::JudgeDecided{task_id,node_id,branch})
                if task_id==&request.task_id && node_id==&request.node_id && request.choices.contains(branch)=>{},
            (EffectIntent::SaveResult{intent},SessionInputKind::ResultSaved{result}) if intent==&result.intent=>{},
            _=>return Err(invalid("effect completion does not match canonical command")),
        }
        if delivery.acked {
            if delivery.completed_event.as_ref()!=Some(&input.event_id) {return Err(conflict("effect completion identity reused"))}
        } else if delivery.until_ms<=self.atomic.server_time_ms().await.map_err(storage)? {return Err(RuntimeError::OwnershipLost)}
        let (position,mut conditions,mut mutations)=self.append_plan(&command.session_id,&input).await?;
        conditions.push(condition(key.clone(),&row));
        if !delivery.acked {
            delivery.acked=true;delivery.completed_event=Some(input.event_id.clone());
            mutations.push(put(key,&delivery)?);
            mutations.push(Mutation::Delete{key:entity("command-ready",&fence.command_id.value)});
        }
        match self.write(&fence.command_id.scope,conditions,mutations).await {
            Err(RuntimeError::Contention)=>{self.effect_delivery(fence).await?;Err(RuntimeError::Contention)},
            result=>{result?;Ok(position)},
        }
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

impl RuntimeStore {
    async fn receive_commands(&self, scope: &Scope, consumer: &NodeIdentity, kind: CommandConsumerKind, after: Option<String>, limit: u32) -> RuntimeResult<Page<LeasedCommand>> {
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
            // Worker leases are renewed by their owned effect, never by a later
            // scheduler scan. A new execution waits for expiry and gets a new epoch.
            if kind==CommandConsumerKind::Worker && delivery.consumer.is_some() && delivery.until_ms>now {continue;}

            if delivery.consumer.as_ref()!=Some(consumer) || delivery.until_ms<=now {
                delivery.epoch=delivery.epoch.checked_add(1).ok_or_else(|| invalid("delivery epoch exhausted"))?;
            }
            delivery.consumer = Some(consumer.clone()); delivery.until_ms = now.checked_add(3000).ok_or_else(|| invalid("lease overflow"))?;
            conditions.push(condition(key.clone(), &row)); mutations.push(put(key, &delivery)?); received.push(LeasedCommand { fence:CommandDeliveryFence { command_id:command.command_id.clone(),consumer:consumer.clone(),epoch:delivery.epoch },command,lease_ms:3000 });
        }
        self.write(scope, conditions, mutations).await?;
        Ok(Page { items: received, next_cursor: cursor(page.next_cursor)? })
    }

    async fn effect_delivery(&self,fence:&CommandDeliveryFence)->RuntimeResult<(String,Option<VersionedValue>,Delivery,Command)> {
        if fence.consumer.scope!=fence.command_id.scope {return Err(invalid("effect consumer scope"))}
        let key=entity("delivery",&fence.command_id.value);
        let mut rows=self.rows(&fence.command_id.scope,&[key.clone(),entity("command",&fence.command_id.value)]).await?;
        let command:Command=decode(rows[1].as_ref().ok_or(RuntimeError::NotFound)?)?;
        let row=rows.remove(0);let delivery:Delivery=decode(row.as_ref().ok_or(RuntimeError::NotFound)?)?;
        if delivery.consumer.as_ref()!=Some(&fence.consumer) || delivery.epoch!=fence.epoch {return Err(RuntimeError::OwnershipLost)}
        Ok((key,row,delivery,command))
    }
}
