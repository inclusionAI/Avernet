use super::*;
use std::collections::VecDeque;
const GROUP: &str = "dispatch-v1";
struct Commands {
    store: StoreDispatch,
    key: String,
    consumer: String,
    backend: BackendId,
    delivered: BTreeMap<String, Vec<StreamId>>,
    claim_after: Option<StreamId>,
    acknowledged: VecDeque<(u64, u64, String)>,
}
impl StoreDispatch {
    pub(super) async fn command_feed(
        &self,
        pool: PoolId,
        me: Instance,
    ) -> Result<Box<dyn CommandFeedPort>, StoreError> {
        self.layout.held(&me)?;
        let key = self.layout.command(&pool, 0)?;
        let consumer = format!("{}:{}:{}", me.node, me.incarnation, uuid::Uuid::new_v4());
        self.io(
            async {
                self.stores
                    .streams
                    .create_group(&key, GROUP, GroupStart::Beginning)
                    .await
                    .map(|_| ())
                    .map_err(|e| crate::error::from_write(&e, &key))
            },
            true,
            &key,
        )
        .await?;
        Ok(Box::new(Commands {
            store: self.clone(),
            backend: BackendId(self.layout.namespace_identity()),
            key,
            consumer,
            delivered: BTreeMap::new(),
            claim_after: None,
            acknowledged: VecDeque::new(),
        }))
    }
}
#[async_trait]
impl CommandFeedPort for Commands {
    async fn receive(
        &mut self,
        max: NonZeroU32,
        timeout: Duration,
    ) -> Result<Vec<CommandDelivery>, StoreError> {
        let available = 128usize.saturating_sub(self.delivered.values().map(Vec::len).sum());
        if available == 0 {
            return Err(unavailable("dispatch_command_inflight"));
        }
        let max = NonZeroU32::new(max.get().min(available as u32))
            .ok_or_else(|| invalid("dispatch_command_count"))?;
        // Recover PEL entries before new deliveries; a reclaimed hint does not grant
        // execution ownership. claim/resume and the exact holder remain authoritative.
        let page = self
            .store
            .io(
                async {
                    self.store
                        .stores
                        .streams
                        .claim_idle(
                            &self.key,
                            GROUP,
                            &self.consumer,
                            Duration::from_secs(30),
                            self.claim_after,
                            max,
                        )
                        .await
                        .map_err(|e| crate::error::from_write(&e, &self.key))
                },
                true,
                &self.key,
            )
            .await?;
        self.claim_after = page.next;
        let mut entries = page.entries;
        if entries.is_empty() {
            let block = if timeout.is_zero() {
                None
            } else {
                Some(timeout.min(Duration::from_millis(100)))
            };
            entries = self
                .store
                .io(
                    async {
                        self.store
                            .stores
                            .streams
                            .read_group(
                                &self.key,
                                GROUP,
                                &self.consumer,
                                ReadOptions {
                                    max_entries: max,
                                    block,
                                },
                            )
                            .await
                            .map_err(|e| crate::error::from_write(&e, &self.key))
                    },
                    true,
                    &self.key,
                )
                .await?;
        }
        let mut result = vec![];
        for entry in entries {
            let bytes = entry
                .fields
                .get("did")
                .ok_or_else(|| invalid("dispatch_command_shape"))?;
            if bytes.len() > 256 {
                return Err(invalid("dispatch_command_size"));
            }
            let did = Did(String::from_utf8(bytes.clone())
                .map_err(|_| invalid("dispatch_command_encoding"))?);
            self.store.layout.disp(&did)?;
            let first = !self.delivered.contains_key(&did.0);
            let pending = self.delivered.entry(did.0.clone()).or_default();
            if !pending.contains(&entry.id) {
                pending.push(entry.id);
            }
            if first {
                result.push(CommandDelivery {
                    did,
                    backend: self.backend.clone(),
                    consumer_generation: self.consumer.clone(),
                    opaque: format!("{}:{}", entry.id.major, entry.id.minor),
                });
            }
        }
        Ok(result)
    }
    async fn ack(&mut self, delivery: &CommandDelivery) -> Result<(), StoreError> {
        if delivery.backend != self.backend || delivery.consumer_generation != self.consumer {
            return Err(invalid("dispatch_command_scope"));
        }
        let (major, minor) = delivery
            .opaque
            .split_once(':')
            .ok_or_else(|| invalid("dispatch_command_token"))?;
        let id = StreamId {
            major: major
                .parse()
                .map_err(|_| invalid("dispatch_command_token"))?,
            minor: minor
                .parse()
                .map_err(|_| invalid("dispatch_command_token"))?,
        };
        let identity = (id.major, id.minor, delivery.did.0.clone());
        if self.acknowledged.contains(&identity) {
            return Ok(());
        }
        let ids = self
            .delivered
            .get(&delivery.did.0)
            .filter(|ids| ids.first() == Some(&id))
            .cloned()
            .ok_or_else(|| invalid("dispatch_command_delivery"))?;
        self.store
            .io(
                async {
                    self.store
                        .stores
                        .streams
                        .ack(&self.key, GROUP, &ids)
                        .await
                        .map(|_| ())
                        .map_err(|e| crate::error::from_write(&e, &self.key))
                },
                true,
                &self.key,
            )
            .await?;
        self.delivered.remove(&delivery.did.0);
        self.acknowledged.push_back(identity);
        if self.acknowledged.len() > 128 {
            self.acknowledged.pop_front();
        }
        Ok(())
    }
}
