//! Follow the canonical committed feed without making its terminal publication
//! authoritative. A feed append may precede the strict round's cold proof.
use super::*;

const AUTHORITY_WAIT: Duration = Duration::from_secs(2);
const AUTHORITY_RECHECK: Duration = Duration::from_millis(250);

impl Shared {
    pub(super) fn group_feed_for(&self, sid: &Sid) -> Option<Arc<StoreTaskFeed>> {
        self.group_feed
            .as_ref()
            .filter(|_| CreationSource::of_sid(sid) == Some(CreationSource::GroupA2a))
            .cloned()
    }

    pub(super) async fn validate_group_resume(
        &self,
        feed: Arc<StoreTaskFeed>,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<ResumeCheck, StoreError> {
        let expected = feed.cursor(&cursor.sid, &cursor.tid, None)?;
        if cursor.token.backend != expected.token.backend
            || cursor.token.generation != expected.token.generation
        {
            return Ok(ResumeCheck::Gap);
        }
        match self.stored(scope, &cursor.tid).await? {
            Some(Stored::StrictArchived(..)) => Ok(ResumeCheck::Gap),
            Some(Stored::Live(snapshot)) if snapshot.sid == cursor.sid => {
                feed.validate_resume(scope, cursor).await
            }
            _ => Ok(ResumeCheck::InvalidScope),
        }
    }
}

fn needs_terminal_proof(body: &OutputBody) -> bool {
    match body {
        OutputBody::Terminal { .. } => true,
        OutputBody::Status { status, .. } => is_terminal(*status),
        OutputBody::Artifact { class, .. } => *class == OutputClass::FinalAnswer,
    }
}

async fn await_terminal(
    shared: &Shared,
    scope: &TaskScope,
    tid: &Tid,
) -> Result<(TerminalStatus, BlobRef), StoreError> {
    // One checkpoint read per recheck; read archive proof/result only once
    // it can be authoritative. The application grant guard keeps polling
    // independently of this wait and drops it when authorization is revoked.
    tokio::time::timeout(AUTHORITY_WAIT, async {
        loop {
            match shared.stored(scope, tid).await? {
                Some(Stored::StrictArchived(record, reference)) => {
                    return Ok((record.status, reference));
                }
                Some(Stored::Live(_)) => tokio::time::sleep(AUTHORITY_RECHECK).await,
                _ => return Err(not_applied("group_follow_authority_missing")),
            }
        }
    })
    .await
    .map_err(|_| not_applied("group_follow_authority_pending"))?
}

pub(super) fn follow(
    shared: Arc<Shared>,
    feed: Arc<StoreTaskFeed>,
    scope: TaskScope,
    after: TaskCursor,
) -> BoxStream<'static, Result<FollowItem, StoreError>> {
    let tid = after.tid.clone();
    let stream = feed.follow(scope.clone(), after);
    futures::stream::try_unfold(
        (stream, None),
        move |(mut stream, mut terminal)| {
            let shared = shared.clone();
            let scope = scope.clone();
            let tid = tid.clone();
            async move {
                let Some(mut item) = stream.next().await.transpose()? else {
                    return Ok(None);
                };
                if let FollowItem::Events { items, .. } = &mut item {
                    // Thinking is an internal event, never a public artifact.
                    items.retain(|event| {
                        !matches!(event.body, OutputBody::Artifact { class: OutputClass::Thinking, .. })
                    });
                    if items.iter().any(|event| needs_terminal_proof(&event.body)) {
                        if terminal.is_none() {
                            terminal = Some(await_terminal(&shared, &scope, &tid).await?);
                        }
                        if let Some((status, reference)) = &terminal {
                            for event in items.iter() {
                                match &event.body {
                                    OutputBody::Terminal { status: event_status, result_ref }
                                        if event_status != status || result_ref != reference =>
                                    {
                                        return Err(invalid("group_follow_terminal_mismatch"));
                                    }
                                    OutputBody::Status { status: event_status, .. }
                                        if is_terminal(*event_status) =>
                                    {
                                        return Err(invalid("group_follow_unarchived_terminal"));
                                    }
                                    OutputBody::Artifact { class: OutputClass::FinalAnswer, .. }
                                        if *status != TerminalStatus::Completed =>
                                    {
                                        return Err(invalid("group_follow_final_artifact_mismatch"));
                                    }
                                    _ => {}
                                }
                            }
                        }
                    }
                }
                Ok(Some((item, (stream, terminal))))
            }
        },
    )
    .boxed()
}
