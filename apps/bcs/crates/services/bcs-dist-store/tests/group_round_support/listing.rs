use super::*;

/// One row of an immutable list cut, plus the contract sort channel.
/// `status_updated_at` is the authoritative checkpoint commit sequence that
/// last fixed the row's status: the terminal commit for completed rounds, the
/// admission commit (`status_seq`) for running rows. The prototype maps the
/// `(status_updated_at DESC, tid ASC)` order through this sequence; production
/// writes a real status timestamp column (T4/T8), not a commit-seq proxy.
pub struct CutRow {
    pub view: GroupRoundView,
    pub status_updated_at: u64,
}
pub struct ListCut { pub rows: Vec<CutRow> }
impl Prototype {
    async fn registry(&self) -> Result<(Option<Version>,Vec<Sid>),StoreError> {
        match self.document("t1-group-registry").await? {
            None => Ok((None,vec![])),
            Some((v,value)) => Ok((Some(v),decode(value)?)),
        }
    }
    pub async fn keys(&self) -> Result<Vec<RoundKey>,StoreError> {
        let (_,sids)=self.registry().await?;
        let mut result=vec![];
        for sid in sids {
            if let Some(e)=self.store.load(&sid).await? {
                for r in records(runner(&e.checkpoint)?)? {
                    result.push(decode(r["accepted"]["input"]["key"].clone())?);
                }
            }
        }
        Ok(result)
    }
    /// Pre-registration ensures every accepted checkpoint is discoverable without
    /// hints. Prepared-only entries are harmless and count against finite capacity.
    pub async fn discover(&self) -> Result<Vec<Sid>,StoreError> {
        let (_,sids)=self.registry().await?;
        let mut result=vec![];
        for sid in sids {
            if let Some(e)=self.store.load(&sid).await? {
                if records(runner(&e.checkpoint)?)?.iter().any(|r|
                    r["terminal"].is_null() && r["dispatched"] != true) { result.push(sid); }
            }
        }
        Ok(result)
    }
    pub async fn project(&self,sid:&Sid) -> Result<(),StoreError> {
        let e=self.store.load(sid).await?.ok_or_else(|| error("checkpoint_missing"))?;
        let projection=json!({"seq":e.checkpoint.commit_seq.0,"state":runner(&e.checkpoint)?});
        let key=format!("projection:{}",sid.0);
        let old=self.document(&key).await?;
        if let Some((_,v))=&old {
            if v["seq"].as_u64() > projection["seq"].as_u64() { return Err(dependency("projection_stale")); }
        }
        if self.replace(&key,old.map(|(v,_)|v),projection).await? { Ok(()) }
        else { Err(dependency("projection_conflict")) }
    }
    /// Bounded double collect: registry + all checkpoint heads before and after
    /// projections must match exactly. Otherwise fail closed (no invented total).
    /// Captured immutable values define this cut, not live page reads. Production
    /// snapshot materialization/cursor retention is still a T8 implementation.
    pub async fn list_cut(&self) -> Result<ListCut,StoreError> {
        self.collect_cut(None).await
    }
    pub async fn list_cut_paused(&self,ready:&tokio::sync::Notify,resume:&tokio::sync::Notify)
        ->Result<ListCut,StoreError> {
        self.collect_cut(Some((ready,resume))).await
    }
    async fn collect_cut(&self,pause:Option<(&tokio::sync::Notify,&tokio::sync::Notify)>)
        ->Result<ListCut,StoreError> {
        let (version,sids)=self.registry().await?;
        let mut heads=vec![];
        for sid in &sids {
            heads.push(self.store.load(sid).await?.map(|e|e.checkpoint));
        }
        let mut rows=vec![];
        for (sid,head) in sids.iter().zip(&heads) {
            let Some(head)=head else { continue };
            let (_,projection)=self.document(&format!("projection:{}",sid.0)).await?
                .ok_or_else(||dependency("projection_lag"))?;
            if projection["seq"] != json!(head.commit_seq.0) || projection["state"] != *runner(head)? {
                return Err(dependency("projection_lag"));
            }
            for r in records(&projection["state"])? {
                let accepted:AcceptedRound=decode(r["accepted"].clone())?;
                let terminal:Option<TerminalProof>=decode(r["terminal"].clone())?;
                let status_updated_at=terminal.as_ref().map(|t|t.commit_seq.0)
                    .or_else(||r["status_seq"].as_u64())
                    .ok_or_else(||error("status_seq_missing"))?;
                // No fresh Get here: it could mix a later terminal into this cut.
                rows.push(CutRow { view:GroupRoundView { accepted,terminal,result:None },
                    status_updated_at });
            }
            // 热转冷已完成的轮次同样属于该 cut, 只经冷摘要核验读回。
            for entry in cold_list(&projection["state"])? {
                let Some(facts)=self.read_cold(entry).await? else {
                    return Err(dependency("cold_missing"));
                };
                let status_updated_at=facts.terminal.as_ref().map(|t|t.commit_seq.0)
                    .ok_or_else(||error("cold_not_terminal"))?;
                rows.push(CutRow { view:GroupRoundView { accepted:facts.accepted,
                    terminal:facts.terminal,result:None },status_updated_at });
            }
        }
        if let Some((ready,resume))=pause {
            ready.notify_one();resume.notified().await;
        }
        for (sid,head) in sids.iter().zip(&heads) {
            if self.store.load(sid).await?.map(|e|e.checkpoint) != *head {
                return Err(dependency("cut_changed"));
            }
        }
        if self.registry().await?.0 != version { return Err(dependency("registry_changed")); }
        Ok(ListCut { rows })
    }
}
