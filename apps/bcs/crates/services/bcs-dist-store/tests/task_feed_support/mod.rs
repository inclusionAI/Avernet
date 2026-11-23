#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    dead_code,
    reason = "provider test fixtures"
)]
use crate::common::{Recorder, SID, TID, scope, task};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_dist_model::*;
use bcs_dist_projection::OutputProjectionService;
use bcs_dist_store::{
    archive::DbArchiveStore,
    identity::digest_json,
    layout::KeyLayout,
    task_feed::{StoreTaskFeed, TaskFeedConfig, TaskFeedStores},
};
use bcs_service_api::port::repo::distributed::{common::*, session::*, task_feed::*};
use bcs_store_api::{document::DocumentStorePlugin, zset::*};
use std::{
    collections::{BTreeMap, VecDeque},
    num::NonZeroU32,
    sync::Arc,
};

pub struct Fixture {
    pub storage: Arc<Recorder>,
    pub archive: Arc<DbArchiveStore>,
    pub layout: KeyLayout,
    pub feed: StoreTaskFeed,
    pub db: Arc<LocalSqliteDbPlugin>,
}
impl Fixture {
    pub async fn new() -> Self {
        let storage = Arc::new(Recorder::default());
        let layout = KeyLayout::new("feed", "test").unwrap();
        let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let archive =
            Arc::new(DbArchiveStore::new(db.clone(), storage.clone(), layout.clone()).unwrap());
        let mut initial = task();
        initial.content.snapshot = Payload::Inline(
            serde_json::to_value(DisplaySnapshot {
                format_version: 1,
                units: vec![],
            })
            .unwrap(),
        );
        storage
            .add(
                &layout.tasks(&scope()).unwrap(),
                &[ZSetEntry {
                    member: TID.into(),
                    score: 0.0,
                }],
                ZAddMode::InsertOnly,
            )
            .await
            .unwrap();
        storage
            .add(
                &layout.session_tasks(&Sid(SID.into())).unwrap(),
                &[ZSetEntry {
                    member: TID.into(),
                    score: 0.0,
                }],
                ZAddMode::InsertOnly,
            )
            .await
            .unwrap();
        storage
            .create_document(
                &layout.task(&Tid(TID.into())).unwrap(),
                &serde_json::to_value(initial).unwrap(),
            )
            .await
            .unwrap();
        let feed = build(&storage, &archive, &layout);
        Self {
            storage,
            archive,
            layout,
            feed,
            db,
        }
    }
    pub fn reopen(&self) -> StoreTaskFeed {
        build(&self.storage, &self.archive, &self.layout)
    }
    pub fn with_config(&self, config: TaskFeedConfig) -> StoreTaskFeed {
        StoreTaskFeed::new(
            TaskFeedStores {
                documents: self.storage.clone(),
                streams: self.storage.clone(),
                index: self.storage.clone(),
                blobs: self.archive.clone(),
                results: self.archive.clone(),
            },
            Arc::new(OutputProjectionService),
            self.layout.clone(),
            config,
        )
        .unwrap()
    }
    pub async fn commit(&self, sequence: u64, kinds: Vec<EffectKind>) -> Vec<EffectSource> {
        let owner = ShardOwner {
            instance: Instance {
                node: "worker".into(),
                incarnation: "one".into(),
            },
            epoch: WorkerEpoch(1),
        };
        let effects: Vec<Effect> = kinds
            .into_iter()
            .enumerate()
            .map(|(index, kind)| Effect {
                id: EffectId {
                    sid: Sid(SID.into()),
                    commit: CommitSeq(sequence),
                    index: NonZeroU32::new(index as u32 + 1).unwrap(),
                },
                content_digest: kind_digest(&kind),
                kind,
            })
            .collect();
        let receipt = CommitReceipt {
            sid: Sid(SID.into()),
            owner: owner.clone(),
            commit_id: CommitId(format!("commit-{sequence}")),
            content_digest: kind_digest(&serde_json::json!({"sequence":sequence})),
            sequence: CommitSeq(sequence),
            consumed: None,
            proof: "opaque".into(),
        };
        let sources = effects
            .iter()
            .map(|effect| EffectSource {
                commit: receipt.clone(),
                effect_id: effect.id.clone(),
                content_digest: effect.content_digest.clone(),
            })
            .collect();
        let envelope = CheckpointEnvelope {
            owner,
            checkpoint: Checkpoint {
                format_version: 1,
                commit_seq: CommitSeq(sequence),
                consumed: None,
                tasks: VecDeque::new(),
                runner_state: None,
                dispatch_seq: BTreeMap::new(),
                deadlines: vec![],
                deadline_history: Default::default(),
                view_drafts: BTreeMap::new(),
                last_event_id: None,
                outbox: Some(CommittedBatch {
                    created_at: None,
                    commit_id: receipt.commit_id,
                    source_commit: receipt.sequence,
                    content_digest: receipt.content_digest,
                    effects,
                }),
            },
            recovery: RecoveryMetadata {
                takeover_streak: 0,
                last_attempt: None,
                quarantined: false,
                last_active_at: TimestampMillis(0),
            },
        };
        let key = self.layout.ckpt(&Sid(SID.into())).unwrap();
        let value = serde_json::to_value(envelope).unwrap();
        if let Some(current) = self.storage.read_document(&key).await.unwrap() {
            self.storage
                .replace_document(&key, current.version, &value)
                .await
                .unwrap();
        } else {
            self.storage.create_document(&key, &value).await.unwrap();
        }
        sources
    }
    pub async fn append_request(
        &self,
        sequence: u64,
        previous: Option<EventId>,
        events: Vec<OutputEvent>,
    ) -> AppendCommittedEvents {
        let sources = self
            .commit(
                sequence,
                vec![EffectKind::AppendEvents {
                    previous,
                    events: events.clone(),
                }],
            )
            .await;
        AppendCommittedEvents {
            source: sources[0].clone(),
            previous,
            events,
        }
    }
}
pub fn hello_snapshot(sequence: u64, covered: EventId) -> TaskSnapshot {
    let mut snapshot = task();
    snapshot.source_commit = CommitSeq(sequence);
    snapshot.snapshot_event_id = Some(covered);
    snapshot.output_usage.cumulative_bytes = 9;
    snapshot.content.snapshot = Payload::Inline(
        serde_json::to_value(DisplaySnapshot {
            format_version: 1,
            units: vec![DisplayUnit {
                node: NodeId("node".into()),
                artifact_id: ArtifactId("answer".into()),
                class: OutputClass::Output,
                content: serde_json::json!("hello"),
            }],
        })
        .unwrap(),
    );
    snapshot
}
fn build(
    storage: &Arc<Recorder>,
    archive: &Arc<DbArchiveStore>,
    layout: &KeyLayout,
) -> StoreTaskFeed {
    StoreTaskFeed::new(
        TaskFeedStores {
            documents: storage.clone(),
            streams: storage.clone(),
            index: storage.clone(),
            blobs: archive.clone(),
            results: archive.clone(),
        },
        Arc::new(OutputProjectionService),
        layout.clone(),
        TaskFeedConfig::default(),
    )
    .unwrap()
}
pub fn kind_digest(value: &impl serde::Serialize) -> Digest {
    digest_json(&serde_json::to_value(value).unwrap())
}
pub fn event_id(commit: u64, index: u32) -> EventId {
    EventId {
        commit: CommitSeq(commit),
        index: NonZeroU32::new(index).unwrap(),
    }
}
pub fn text_events(commit: u64) -> Vec<OutputEvent> {
    [
        ArtifactUpdate::Upsert {
            content: Payload::Inline(serde_json::json!("he")),
        },
        ArtifactUpdate::AppendText {
            delta: Payload::Inline(serde_json::json!("llo")),
        },
    ]
    .into_iter()
    .enumerate()
    .map(|(index, update)| OutputEvent {
        id: event_id(commit, index as u32 + 1),
        tid: Tid(TID.into()),
        body: OutputBody::Artifact {
            node: NodeId("node".into()),
            artifact_id: ArtifactId("answer".into()),
            class: OutputClass::Output,
            update,
        },
    })
    .collect()
}
pub fn display(view: &TaskView) -> DisplaySnapshot {
    let Payload::Inline(value) = &view.content.snapshot else {
        panic!("resolved snapshot")
    };
    serde_json::from_value(value.clone()).unwrap()
}
