//! S1.1 lifecycle closure tests: cold-proof transfer, checkpoint compaction,
//! expiry tombstones, fenced recycling and the bounded capacity plan. These
//! run on the test Model documents and real checkpoint CAS; production SQL and
//! runtime assembly stay in T2/T4/T7.
#![allow(clippy::unwrap_used, reason = "protocol assertions")]
#[path = "group_round_support/mod.rs"]
mod support;
use support::*;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::group_round::{
    GroupRequestIdentity, GroupRequestRecord, GroupRoundRepo as _, GroupStartRequest,
};

async fn record_of(p: &Prototype, request: &GroupStartRequest)
    -> Result<GroupRequestRecord, bcs_service_api::port::repo::distributed::common::StoreError>
{
    let identity = GroupRequestIdentity::strict(&request.input.key.scope,
        &request.input.key.group, &request.request_id);
    p.lookup(&identity).await
}

async fn complete(p: &Prototype, key: &RoundKey)
    -> Result<(), bcs_service_api::port::repo::distributed::common::StoreError>
{
    p.dispatch(key).await?;
    p.archive(key, "answer-a").await?;
    p.stop_remote(key).await?;
    p.finish(key).await
}

/// Fail-closed capacity refusals are dependency errors with a stable code; a
/// success here (or an unrelated error) would silently weaken the bound.
fn assert_not_applied<T>(
    result: Result<T, bcs_service_api::port::repo::distributed::common::StoreError>,
    code: &str)
{
    let Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied { code: actual })
        = result else { panic!("expected NotApplied({code})") };
    assert_eq!(actual, code);
}

#[tokio::test]
async fn full_lifecycle_compacts_cold_recovers_same_tid_and_expires() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    // 受理: an accepted round is readable, but never publicized as success
    // before its authoritative terminal commit.
    assert!(matches!(p.admit(&a).await.unwrap(), Admission::New { .. }));
    let view = p.get(&a.input.key).await.unwrap().unwrap();
    assert!(view.terminal.is_none());
    assert!(view.result.is_none());
    complete(&p, &a.input.key).await.unwrap();
    assert!(p.get(&a.input.key).await.unwrap().unwrap().terminal.is_some());
    assert_eq!(p.hot_round_count(&a.input.key.sid).await.unwrap(), 1);
    assert_eq!(p.cold_round_count(&a.input.key.sid).await.unwrap(), 0);
    // 冷转存 + 读回确认 + checkpoint 压缩
    p.compact(&a.input.key).await.unwrap();
    p.compact(&a.input.key).await.unwrap(); // 幂等
    assert_eq!(p.hot_round_count(&a.input.key.sid).await.unwrap(), 0);
    assert_eq!(p.cold_round_count(&a.input.key.sid).await.unwrap(), 1);
    let cold = p.get(&a.input.key).await.unwrap().unwrap();
    assert_eq!(cold.result, Some(Payload::Inline(serde_json::json!("answer-a"))));
    assert!(cold.terminal.is_some());
    // owner 交接
    let q = p.takeover().await;
    // 原请求重试: 压缩与接管之后仍恢复同一 Tid
    match q.admit(&a).await.unwrap() {
        Admission::Replay { accepted } => assert_eq!(accepted.input.key, a.input.key),
        other => panic!("expected replay, got {other:?}"),
    }
    assert_eq!(record_of(&q, &a).await.unwrap(), GroupRequestRecord::Accepted(a.clone()));
    // 结果过期: 墓碑先可读
    q.expire_tombstone(&a.input.key).await.unwrap();
    assert_eq!(record_of(&q, &a).await.unwrap(), GroupRequestRecord::Expired);
    assert_eq!(q.admit(&a).await.unwrap(), Admission::Expired);
    // 墓碑后、证明删除前, 终态结果仍按证明可读
    assert_eq!(q.get(&a.input.key).await.unwrap().unwrap().result,
        Some(Payload::Inline(serde_json::json!("answer-a"))));
    // 后删证明/结果
    q.expire_cleanup(&a.input.key).await.unwrap();
    assert!(q.get(&a.input.key).await.unwrap().is_none());
    assert_eq!(q.cold_round_count(&a.input.key.sid).await.unwrap(), 0);
    // 过期不能重跑
    assert_eq!(q.admit(&a).await.unwrap(), Admission::Expired);
}

#[tokio::test]
async fn cold_transfer_is_readback_verified_and_get_revalidates_digest() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.admit(&a).await.unwrap();
    complete(&p, &a.input.key).await.unwrap();
    // 转存写入丢响应: 未读回确认就不能压缩热证据
    p.lose_next_document_reply();
    assert!(p.compact(&a.input.key).await.is_err());
    assert_eq!(p.hot_round_count(&a.input.key.sid).await.unwrap(), 1);
    // 已存在的冷文档与热证据不符: 读回核对拒绝压缩
    p.corrupt_cold(&a.input.key).await.unwrap();
    assert!(p.compact(&a.input.key).await.is_err());
    assert_eq!(p.hot_round_count(&a.input.key.sid).await.unwrap(), 1);
    p.repair_cold(&a.input.key).await.unwrap();
    p.compact(&a.input.key).await.unwrap();
    assert_eq!(p.hot_round_count(&a.input.key.sid).await.unwrap(), 0);
    assert_eq!(p.cold_round_count(&a.input.key.sid).await.unwrap(), 1);
    // 压缩后冷文档被篡改: 每次读回重新计算摘要
    p.corrupt_cold(&a.input.key).await.unwrap();
    assert!(p.get(&a.input.key).await.is_err());
    assert!(p.feed_permission(&a.input.key).await.is_err());
}

#[tokio::test]
async fn stale_owner_writes_are_rejected_across_lifecycle_windows() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.admit(&a).await.unwrap();
    complete(&p, &a.input.key).await.unwrap();
    p.compact(&a.input.key).await.unwrap();
    let q = p.takeover().await;
    let b = request("a", "message-b", 2);
    // 旧 owner 的迟到受理/完成/压缩/过期写全部被 owner 栅栏拒绝
    assert!(p.admit(&b).await.is_err());
    assert!(p.finish(&a.input.key).await.is_err());
    assert!(p.compact(&a.input.key).await.is_err());
    assert!(p.expire_tombstone(&a.input.key).await.is_err());
    assert!(p.expire_cleanup(&a.input.key).await.is_err());
    // 被拒的旧写不改变任何可见状态
    assert_eq!(q.get(&a.input.key).await.unwrap().unwrap().result,
        Some(Payload::Inline(serde_json::json!("answer-a"))));
    // 新 owner 继续受理下一轮
    assert!(matches!(q.admit(&b).await.unwrap(), Admission::New { .. }));
}

#[tokio::test]
async fn fresh_owner_from_shard_record_dispatches_discovered_work_without_hints() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.admit(&a).await.unwrap();
    // 无 hint、无进程内存: 接任者只从权威 shard 记录激活
    let q = p.recover().await;
    let work = q.pending_work().await.unwrap();
    assert_eq!(work.len(), 1);
    let discovered = work[0].clone();
    // 只按目录 + checkpoint 推导执行
    complete(&q, &discovered).await.unwrap();
    assert_eq!(q.get(&discovered).await.unwrap().unwrap().result,
        Some(Payload::Inline(serde_json::json!("answer-a"))));
    // 发现的待执行项与原受理记录必须指向同一轮次
    match q.admit(&a).await.unwrap() {
        Admission::Replay { accepted } => assert_eq!(accepted.input.key, discovered),
        other => panic!("expected replay, got {other:?}"),
    }
    assert!(p.dispatch(&discovered).await.is_err());
}

#[tokio::test]
async fn expiry_tombstone_blocks_rerun_before_and_after_proof_deletion() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.admit(&a).await.unwrap();
    complete(&p, &a.input.key).await.unwrap();
    p.compact(&a.input.key).await.unwrap();
    p.expire_tombstone(&a.input.key).await.unwrap();
    // 删除窗口内: 过期已生效, 终态结果仍按证明可读
    assert_eq!(p.admit(&a).await.unwrap(), Admission::Expired);
    assert_eq!(record_of(&p, &a).await.unwrap(), GroupRequestRecord::Expired);
    assert!(p.get(&a.input.key).await.unwrap().is_some());
    // 同会话的下一轮不受墓碑影响
    let b = request("a", "message-b", 2);
    assert!(matches!(p.admit(&b).await.unwrap(), Admission::New { .. }));
    p.expire_cleanup(&a.input.key).await.unwrap();
    // 清理后: 无证明可读, 但墓碑照样拒绝重跑
    assert!(p.get(&a.input.key).await.unwrap().is_none());
    assert_eq!(p.admit(&a).await.unwrap(), Admission::Expired);
    assert_eq!(p.cold_round_count(&a.input.key.sid).await.unwrap(), 0);
    assert_eq!(p.hot_round_count(&a.input.key.sid).await.unwrap(), 1);
}

#[tokio::test]
async fn bounded_round_history_rejects_when_full_and_recovers_after_expiry() {
    let p = Prototype::with_bounds(Bounds { max_rounds_per_sid: 2, ..Bounds::default() }).await;
    let a = request("a", "round-one", 1);
    p.admit(&a).await.unwrap();
    complete(&p, &a.input.key).await.unwrap();
    p.compact(&a.input.key).await.unwrap();
    let b = request("a", "round-two", 2);
    p.admit(&b).await.unwrap();
    complete(&p, &b.input.key).await.unwrap();
    p.compact(&b.input.key).await.unwrap();
    assert_eq!(p.cold_round_count(&a.input.key.sid).await.unwrap(), 2);
    // 满时拒绝
    let c = request("a", "round-three", 3);
    assert_not_applied(p.admit(&c).await, "round_capacity");
    // 过期回收冷证明以恢复容量
    p.expire_tombstone(&a.input.key).await.unwrap();
    p.expire_cleanup(&a.input.key).await.unwrap();
    assert!(matches!(p.admit(&c).await.unwrap(), Admission::New { .. }));
    // 被回收的过期请求不可重跑, 保留的请求仍恢复原 Tid
    assert_eq!(p.admit(&a).await.unwrap(), Admission::Expired);
    match p.admit(&b).await.unwrap() {
        Admission::Replay { accepted } => assert_eq!(accepted.input.key, b.input.key),
        other => panic!("expected replay, got {other:?}"),
    }
}

#[tokio::test]
async fn bounded_directory_rejects_new_sids_until_fenced_recycle_frees_capacity() {
    let p = Prototype::with_bounds(Bounds { max_sids: 1, ..Bounds::default() }).await;
    let a = request("a", "message-a", 1);
    p.admit(&a).await.unwrap();
    complete(&p, &a.input.key).await.unwrap();
    p.compact(&a.input.key).await.unwrap();
    let b = request("b", "message-b", 1);
    assert_not_applied(p.prepare(&b).await, "registry_capacity"); // 满时拒绝
    let q = p.takeover().await;
    // 未决预留阻止回收
    let pending = request("a", "pending", 2);
    let held = q.reserve_only(&pending).await.unwrap();
    assert_not_applied(q.recycle_directory(&a.input.key.sid).await, "recycle_pending");
    // 回收前先证旧提交被栅栏阻断: 旧 owner 迟到写失败
    assert!(p.finish(&a.input.key).await.is_err());
    assert!(p.compact(&a.input.key).await.is_err());
    q.abandon(&held).await.unwrap();
    // 过期后整 Sid 回收
    q.expire_tombstone(&a.input.key).await.unwrap();
    q.expire_cleanup(&a.input.key).await.unwrap();
    q.recycle_directory(&a.input.key.sid).await.unwrap();
    assert!(q.prepare(&b).await.is_ok()); // 恢复
    assert!(matches!(q.admit(&b).await.unwrap(), Admission::New { .. }));
    assert_eq!(q.pending_work().await.unwrap(), vec![b.input.key.clone()]);
}

#[tokio::test]
async fn bounded_request_registry_recovers_via_abandon_not_tombstones() {
    let p = Prototype::with_bounds(Bounds { max_requests: 1, ..Bounds::default() }).await;
    let a = request("s1", "message-a", 1);
    let b = request("s2", "message-b", 1);
    let c = request("s3", "message-c", 1);
    let ra = p.reserve_only(&a).await.unwrap();
    assert_not_applied(p.reserve_only(&b).await, "request_capacity"); // 满时拒绝
    p.abandon(&ra).await.unwrap();              // 栅栏后废弃
    assert_eq!(record_of(&p, &a).await.unwrap(), GroupRequestRecord::Absent);
    let rb = p.reserve_only(&b).await.unwrap(); // 废弃后恢复
    assert!(matches!(p.commit_reserved(&rb).await.unwrap(), Admission::New { .. }));
    // 有界回收: 只删除已证明栅栏阻断的废弃行
    assert!(p.recycle_abandoned().await.unwrap() >= 1);
    assert_eq!(p.request_registry_rows().await.unwrap(), 1);
    assert_eq!(record_of(&p, &b).await.unwrap(), GroupRequestRecord::Accepted(b.clone()));
    complete(&p, &b.input.key).await.unwrap();
    p.expire_tombstone(&b.input.key).await.unwrap();
    // 过期墓碑占位: 原型明确拒绝墓碑回收, 容量未恢复
    assert_eq!(record_of(&p, &b).await.unwrap(), GroupRequestRecord::Expired);
    assert_not_applied(p.reserve_only(&c).await, "request_capacity");
}

#[tokio::test]
async fn oversized_round_record_is_rejected_before_commit() {
    let p = Prototype::with_bounds(Bounds { max_record_bytes: 8 * 1024, ..Bounds::default() }).await;
    let mut big = request("a", "big", 1);
    big.input.ordered_parts = vec![RoundPart::Text { text: "x".repeat(16 * 1024) }];
    assert_not_applied(p.admit(&big).await, "record_budget");
    assert!(p.get(&big.input.key).await.unwrap().is_none());
    let small = request("a", "small", 1);
    assert!(matches!(p.admit(&small).await.unwrap(), Admission::New { .. }));
}

#[tokio::test]
async fn publication_permission_survives_compaction_and_takeover() {
    let p = Prototype::new().await;
    let a = request("a", "message-a", 1);
    p.admit(&a).await.unwrap();
    complete(&p, &a.input.key).await.unwrap();
    p.compact(&a.input.key).await.unwrap();
    let q = p.takeover().await;
    q.append_feed(&a.input.key).await.unwrap();
    let r = q.takeover().await;
    r.publish_snapshot(&a.input.key).await.unwrap();
    r.publish_snapshot(&a.input.key).await.unwrap();
    assert_eq!(r.feed_events(&a.input.key.sid).await.unwrap().len(), 2);
    assert_eq!(r.feed_snapshot(&a.input.key).await.unwrap()["result"], "answer-a");
}
