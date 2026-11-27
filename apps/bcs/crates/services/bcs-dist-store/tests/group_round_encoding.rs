//! S1.3 encoding-budget and bounded-failure finalization tests. All budgets
//! exercised here are explicit small values (boundary forcing) or the real
//! candidate/magnitude constants — test numbers are NOT production defaults.
//! Production boundaries land in T4/T6/T7/T8, not in this fixture.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "protocol assertions")]
#[path = "group_round_support/mod.rs"]
mod support;
use support::encoding::{
    ChunkSource, artifacts_result, artifact_event, chain_parts, encoded_len, frame_value,
    inbound_envelope, part_values, solve_filler,
};
use support::{request, Prototype};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::group_round::{GroupRoundRepo, RoundBudgetProfile};
use bcs_test_support::contract::distributed::group_round::read_round_payload;
use serde_json::{json, Value};

/// Explicit small budgets (test-only boundary forcing; NOT default config).
fn small_profile(event_bytes: usize, batch_bytes: usize, frame_bytes: usize) -> RoundBudgetProfile {
    RoundBudgetProfile {
        inbound_bytes: 8 * 1024 * 1024,
        public_frame_bytes: frame_bytes,
        feed_event_bytes: event_bytes,
        feed_batch_bytes: batch_bytes,
        cursor_lifetime_millis: 300_000,
        max_page_rows: 100,
    }
}

fn assert_not_applied<T>(result: Result<T, StoreError>, code: &str) {
    let Err(StoreError::NotApplied { code: actual }) = result
        else { panic!("expected NotApplied({code})") };
    assert_eq!(actual, code);
}

#[tokio::test]
async fn inbound_budget_governs_the_whole_resolved_envelope_at_exact_positions() {
    let p = Prototype::new().await;
    let entries = vec![json!({"round": 1, "digest": "\"d1\""}), json!({"round": 2, "digest": "d2"})];
    let history = p.pin_history(&entries).await.unwrap();
    let history_doc = json!({"entries": entries});
    let budget = 4096usize;
    for (target, fits) in [(budget - 1, true), (budget, true), (budget + 1, false)] {
        let fill = solve_filler(target, |n| {
            let mut input = request("a", "message-a", 1).input;
            input.ordered_parts = chain_parts(n, 'x');
            input.history_cut = history.clone();
            inbound_envelope(&input, &history_doc).unwrap()
        }).unwrap();
        let mut round = request("a", "message-a", 1);
        round.input.ordered_parts = chain_parts(fill, 'x');
        round.input.history_cut = history.clone();
        match p.resolve_inbound(&round.input, budget).await {
            Ok(envelope) => {
                assert!(fits, "target {target} must not fit");
                assert_eq!(encoded_len(&envelope).unwrap(), target);
                // 完整读回: the resolved envelope round-trips parts and history.
                let parts: Vec<RoundPart> =
                    serde_json::from_value(envelope["ordered_parts"].clone()).unwrap();
                assert_eq!(parts, round.input.ordered_parts);
                let history_back: Vec<Value> =
                    serde_json::from_value(envelope["history_entries"].clone()).unwrap();
                assert_eq!(history_back, entries);
            }
            Err(e) => {
                assert!(!fits, "target {target} must fit");
                assert_not_applied(Err::<(), _>(e), "inbound_budget");
                let rejected = inbound_envelope(&round.input, &history_doc).unwrap();
                assert_eq!(encoded_len(&rejected).unwrap(), target);
            }
        }
    }
}

#[tokio::test]
async fn inbound_escaping_counts_as_encoded_bytes_not_raw_bytes() {
    let p = Prototype::new().await;
    let history = p.pin_history(&[]).await.unwrap();
    let history_doc = json!({"entries": []});
    let budget = 2048usize;
    // 裸 ASCII 填充恰好到达 B: 被接受
    let plain = solve_filler(budget, |n| {
        let mut input = request("a", "message-a", 1).input;
        input.ordered_parts = chain_parts(n, 'x');
        input.history_cut = history.clone();
        inbound_envelope(&input, &history_doc).unwrap()
    }).unwrap();
    let mut fits = request("a", "message-a", 1);
    fits.input.ordered_parts = chain_parts(plain, 'x');
    fits.input.history_cut = history.clone();
    p.resolve_inbound(&fits.input, budget).await.unwrap();
    // 同字符数的转义样本: 每个 '"' 在 JSON 编码后占 2 字节 → 必然超限,
    // 证明预算数的是编码后字节, 不是裸字节读取。
    let mut escapes = request("a", "message-a", 1);
    escapes.input.ordered_parts = chain_parts(plain, '"');
    escapes.input.history_cut = history.clone();
    let escaped = inbound_envelope(&escapes.input, &history_doc).unwrap();
    assert_eq!(encoded_len(&escaped).unwrap(), budget + plain);
    assert_not_applied(p.resolve_inbound(&escapes.input, budget).await, "inbound_budget");
}

#[test]
fn inbound_boundary_math_holds_at_production_magnitude_without_a_loaded_blob() {
    let budget = PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES;
    for (avail, fits) in [(budget - 1, true), (budget, true), (budget + 1, false)] {
        // 流式提供字节: 检测路径读到 B+1 即止, 超大对象从不完整载入内存。
        let mut source = ChunkSource::new(avail);
        match read_round_payload(&mut source, budget) {
            Ok(bytes) => {
                assert!(fits, "avail {avail} must not fit");
                assert_eq!(bytes.len(), avail);
            }
            Err(_) => assert!(!fits, "avail {avail} must fit"),
        }
        assert_eq!(source.read, if fits { avail } else { budget + 1 });
    }
    // 9 MiB 中间产物: 拒绝发生在 8 MiB+1 读取处
    let mut nine = ChunkSource::new(9 * 1024 * 1024);
    assert!(read_round_payload(&mut nine, budget).is_err());
    assert_eq!(nine.read, budget + 1);
    // 几 KB 候选最终产物在限额内可完整读取 (不能豁免大中间结果, 见 barrier test)
    let mut small = ChunkSource::new(2 * 1024);
    let candidate = read_round_payload(&mut small, budget).unwrap();
    assert_eq!(candidate.len(), 2 * 1024);
}

#[tokio::test]
async fn feed_event_boundaries_choose_inline_or_digest_ref_by_actual_encoding() {
    let p = Prototype::new().await;
    // 事件 512 B / 批 2 KiB / 帧 1 MiB: 显式小预算, 非默认配置。
    let profile = small_profile(512, 2048, 1024 * 1024);
    for (g, target, expect_ref) in [(1u64, 511usize, false), (2, 512, false), (3, 513, true)] {
        let round = request("a", &format!("m{g}"), g);
        p.admit(&round).await.unwrap();
        // 校准目标就是真实发布事件对象 (同一 builder), 位置是承诺的 commit 序列。
        let seq = p.commit_head(&round.input.key.sid).await.unwrap() + 1;
        let fill = solve_filler(target, |n| artifact_event(&round.input.key, seq,
            &artifacts_result(&chain_parts(n, 'x')).unwrap())).unwrap();
        p.archive_value(&round.input.key, artifacts_result(&chain_parts(fill, 'x')).unwrap())
            .await.unwrap();
        p.finish_profiled(&round.input.key, &profile).await.unwrap();
        let view = p.get(&round.input.key).await.unwrap().unwrap();
        assert_eq!(view.terminal.clone().unwrap().status, TerminalStatus::Completed);
        let permit = p.feed_permission(&round.input.key).await.unwrap();
        let event = permit["events"][0].clone();
        assert_eq!(event["id"], json!([seq, 1]));
        if expect_ref {
            // 超过内联预算: 带摘要的归档引用, 引用外壳自身必须通过校验。
            assert_eq!(event["kind"], "artifact_ref");
            assert_eq!(event["ref"]["key"], format!("archive:{}", round.input.key.tid.0));
        } else {
            assert_eq!(event["kind"], "artifact");
            assert_eq!(encoded_len(&event).unwrap(), target);
            assert_eq!(event["result"]["artifacts"],
                serde_json::to_value(part_values(&chain_parts(fill, 'x')).unwrap()).unwrap());
        }
    }
    // 引用事件可公开还原: 按批前驱顺序 append, 同一 digest 从已发布快照完整还原。
    for key in p.keys().await.unwrap() { p.append_feed(&key).await.unwrap(); }
    let referenced = p.keys().await.unwrap()[2].clone();
    p.publish_snapshot(&referenced).await.unwrap();
    let via_sse = p.sse_frame(&referenced, &profile).await.unwrap();
    assert_eq!(via_sse.value["status"], "completed");
    assert!(via_sse.value["result"]["artifacts"][2]["text"].as_str().unwrap().ends_with('x'));
}

#[tokio::test]
async fn feed_batch_shell_overflow_falls_to_bounded_failure_never_completed() {
    let p = Prototype::new().await;
    // 事件 4 KiB / 批 1 KiB: 单事件可编, 批外壳成为受测边界。
    let profile = small_profile(4096, 1024, 1024 * 1024);
    for (g, target, completed) in [(1u64, 1023usize, true), (2, 1024, true), (3, 1025, false)] {
        let round = request("a", &format!("b{g}"), g);
        p.admit(&round).await.unwrap();
        let seq = p.commit_head(&round.input.key.sid).await.unwrap() + 1;
        let fill = solve_filler(target, |n| json!([
            artifact_event(&round.input.key, seq,
                &artifacts_result(&chain_parts(n, 'x')).unwrap()),
            support::encoding::terminal_event(&round.input.key, seq, "completed", None),
        ])).unwrap();

        p.archive_value(&round.input.key, artifacts_result(&chain_parts(fill, 'x')).unwrap())
            .await.unwrap();
        p.finish_profiled(&round.input.key, &profile).await.unwrap();
        let view = p.get(&round.input.key).await.unwrap().unwrap();
        let terminal = view.terminal.clone().unwrap();
        if completed {
            // B−1/B: 批外壳恰好限内 → Completed, 事件批可完整读回。
            assert_eq!(terminal.status, TerminalStatus::Completed);
            let permit = p.feed_permission(&round.input.key).await.unwrap();
            assert_eq!(encoded_len(&permit["events"]).unwrap(), target);
        } else {
            // B+1: 引用和批外壳都必须通过校验, 否则不提交成功;
            // 走有界失败收尾, 绝不公开 Completed。
            assert_eq!(terminal.status, TerminalStatus::Failed);
            assert_eq!(view.result, Some(Payload::Inline(
                json!({"failed": true, "reason": "feed_batch_budget"}))));
            let permit = p.feed_permission(&round.input.key).await.unwrap();
            let events = permit["events"].as_array().unwrap().clone();
            assert_eq!(events.len(), 1);
            assert_eq!(events[0]["kind"], "terminal");
            assert_eq!(events[0]["status"], "failed");
            assert_eq!(events[0]["reason"], "feed_batch_budget");
            // 产物事件从未进入内部 Feed, 失败事件有稳定身份。
            assert_eq!(events[0]["id"], json!([terminal.commit_seq.0, 2]));
        }
    }
}

#[tokio::test]
async fn blob_fits_but_wire_overflows_rejects_the_exact_frame_bound() {
    let p = Prototype::new().await;
    // 裸 Blob 小于帧预算, 但完整 wire = 外壳+parts+history+result:
    // B−1/B 可完整读回, B+1 只能进入有界失败收尾。
    let profile = small_profile(65536, 131072, 2048);
    for (g, target, completed) in [(1u64, 2047usize, true), (2, 2048, true), (3, 2049, false)] {
        let round = request("a", &format!("w{g}"), g);
        p.admit(&round).await.unwrap();
        let seq = p.commit_head(&round.input.key.sid).await.unwrap() + 1;
        let fill = solve_filler(target, |n| frame_value(
            &serde_json::to_value(&round.input.ordered_parts).unwrap(), &json!([]),
            &artifacts_result(&chain_parts(n, 'x')).unwrap(),
            &round.input.key, "completed", seq).unwrap()).unwrap();
        p.archive_value(&round.input.key, artifacts_result(&chain_parts(fill, 'x')).unwrap())
            .await.unwrap();
        p.finish_profiled(&round.input.key, &profile).await.unwrap();
        let view = p.get(&round.input.key).await.unwrap().unwrap();
        let terminal = view.terminal.clone().unwrap();
        if completed {
            assert_eq!(terminal.status, TerminalStatus::Completed);
            // 成功提交的产物可以完整读回 (公开帧按承诺位置命中)。
            let frame = p.round_frame(&round.input.key, 0, &profile).await.unwrap();
            assert_eq!(frame.encoded_bytes, target);
            assert_eq!(frame.value["result"]["artifacts"][2]["text"], "x".repeat(fill));
        } else {
            assert_eq!(terminal.status, TerminalStatus::Failed);
            assert_eq!(view.result, Some(Payload::Inline(
                json!({"failed": true, "reason": "public_frame_budget"}))));
        }
    }
}

#[tokio::test]
async fn public_frame_boundary_holds_at_the_candidate_eight_mib() {
    let p = Prototype::new().await;
    let profile = RoundBudgetProfile::v1();
    let boundary = profile.public_frame_bytes;
    for (g, target, completed) in
        [(1u64, boundary - 1, true), (2, boundary, true), (3, boundary + 1, false)]
    {
        let round = request("a", &format!("e{g}"), g);
        p.admit(&round).await.unwrap();
        let seq = p.commit_head(&round.input.key.sid).await.unwrap() + 1;
        let fill = solve_filler(target, |n| frame_value(
            &serde_json::to_value(&round.input.ordered_parts).unwrap(), &json!([]),
            &artifacts_result(&chain_parts(n, 'x')).unwrap(),
            &round.input.key, "completed", seq).unwrap()).unwrap();
        p.archive_value(&round.input.key, artifacts_result(&chain_parts(fill, 'x')).unwrap())
            .await.unwrap();
        p.finish_profiled(&round.input.key, &profile).await.unwrap();
        let view = p.get(&round.input.key).await.unwrap().unwrap();
        let terminal = view.terminal.clone().unwrap();
        if completed {
            assert_eq!(terminal.status, TerminalStatus::Completed);
            // 8 MiB 量级事件本身走 digest 引用 (128 KiB 事件预算)。
            let permit = p.feed_permission(&round.input.key).await.unwrap();
            assert_eq!(permit["events"][0]["kind"], "artifact_ref");
            // 成功提交的产物可以完整读回, 公开帧恰好命中承诺位置。
            let frame = p.round_frame(&round.input.key, 0, &profile).await.unwrap();
            assert_eq!(frame.encoded_bytes, target);
            p.append_feed(&round.input.key).await.unwrap();
            p.publish_snapshot(&round.input.key).await.unwrap();
            // T8 同一 digest 还原: SSE 通道与公开 Get 一致且完整。
            let via_sse = p.sse_frame(&round.input.key, &profile).await.unwrap();
            assert_eq!(via_sse.value["result"]["artifacts"][2]["text"], "x".repeat(fill));
            assert_eq!(via_sse.encoded_bytes, target);
        } else {
            // B+1: 绝不公开 Completed, 进入有界失败收尾。
            assert_eq!(terminal.status, TerminalStatus::Failed);
            assert_eq!(view.result, Some(Payload::Inline(
                json!({"failed": true, "reason": "public_frame_budget"}))));
        }
    }
}


#[tokio::test]
async fn same_ordered_artifacts_round_trip_inbound_feed_public_get_and_sse() {
    let p = Prototype::new().await;
    let entries = vec![json!({"round": 1, "summary": "\"引号\" \\ 尾"}),
        json!({"round": 2, "summary": "\n\t中"})];
    let history = p.pin_history(&entries).await.unwrap();
    let history_doc = json!({"entries": entries});
    let mut round = request("a", "chain", 1);
    round.input.ordered_parts = chain_parts(0, 'x');
    round.input.history_cut = history.clone();
    // 入站: 外壳 + 转义 parts + 摘要引用 + 解引用后的历史, 完整编码可解。
    let envelope = p.resolve_inbound(&round.input, 8 * 1024 * 1024).await.unwrap();
    assert_eq!(envelope["history_entries"], history_doc["entries"]);
    let parts_back: Vec<RoundPart> =
        serde_json::from_value(envelope["ordered_parts"].clone()).unwrap();
    assert_eq!(parts_back, round.input.ordered_parts);
    p.admit(&round).await.unwrap();
    p.archive_value(&round.input.key, artifacts_result(&round.input.ordered_parts).unwrap())
        .await.unwrap();
    p.finish_profiled(&round.input.key, &RoundBudgetProfile::v1()).await.unwrap();
    p.append_feed(&round.input.key).await.unwrap();
    p.publish_snapshot(&round.input.key).await.unwrap();
    let ordered = serde_json::to_value(&round.input.ordered_parts).unwrap();
    // 内部 Feed: 同一组有序 artifacts 走内联事件。
    let permit = p.feed_permission(&round.input.key).await.unwrap();
    assert_eq!(permit["events"][0]["kind"], "artifact");
    assert_eq!(permit["events"][0]["result"]["artifacts"], ordered);
    // 公开 Get: 同一组有序 artifacts + 默认 0 条历史; 非默认投影解引用历史。
    let frame = p.round_frame(&round.input.key, 0, &RoundBudgetProfile::v1()).await.unwrap();
    assert_eq!(frame.value["parts"], ordered);
    assert_eq!(frame.value["history"], json!([]));
    assert_eq!(frame.value["result"]["artifacts"], ordered);
    let projected = p.round_frame(&round.input.key, 2, &RoundBudgetProfile::v1()).await.unwrap();
    assert_eq!(projected.value["history"], history_doc["entries"]);
    // SSE: 同一 digest 还原, 三种表示完全一致。
    let via_sse = p.sse_frame(&round.input.key, &RoundBudgetProfile::v1()).await.unwrap();
    assert_eq!(via_sse.value["result"]["artifacts"], ordered);
    assert_eq!(via_sse.value["status"], frame.value["status"]);
}

#[tokio::test]
async fn non_default_history_projection_counts_toward_the_frame_and_is_rejected_whole() {
    let p = Prototype::new().await;
    // 大历史行: 非默认投影必须整体计入公开帧预算, 编不下就拒绝整个投影。
    let entries = vec![json!({"filler": "\"x\"".repeat(2048)})];
    let history = p.pin_history(&entries).await.unwrap();
    let mut round = request("a", "projected", 1);
    round.input.history_cut = history.clone();
    p.admit(&round).await.unwrap();
    p.archive_value(&round.input.key, json!({"ok": true})).await.unwrap();
    p.finish_profiled(&round.input.key, &RoundBudgetProfile::v1()).await.unwrap();
    let v1 = RoundBudgetProfile::v1();
    // 校准: 默认投影 (0 条) 的帧长, 作为显式小预算 (非默认配置)。
    let default_frame = p.round_frame(&round.input.key, 0, &v1).await.unwrap();
    let profile = small_profile(128 * 1024, 256 * 1024, default_frame.encoded_bytes);
    assert!(p.round_frame(&round.input.key, 0, &profile).await.is_ok());
    // 非默认投影把历史行计入后必然超预算 → 整体拒绝, 绝不截断产物。
    assert_not_applied(p.round_frame(&round.input.key, 1, &profile).await,
        "projection_frame_budget");
    // 超过 100 条历史行的投影直接被 profile 拒绝。
    let over = p.round_frame(&round.input.key, 101, &v1).await;
    let Err(StoreError::Invalid { code }) = over else { panic!("expected projection limits") };
    assert_eq!(code, "projection_limits");
}

#[tokio::test]
async fn digest_referenced_publication_survives_corruption_loss_and_recovers() {
    let p = Prototype::new().await;
    // 事件 256 B: 产物必然走 digest 引用 (内部可写)。
    let profile = small_profile(256, 1024, 1024 * 1024);
    let round = request("a", "pinned", 1);
    p.admit(&round).await.unwrap();
    let original = artifacts_result(&chain_parts(48, '"')).unwrap();
    p.archive_value(&round.input.key, original.clone()).await.unwrap();
    p.finish_profiled(&round.input.key, &profile).await.unwrap();
    p.append_feed(&round.input.key).await.unwrap();
    p.publish_snapshot(&round.input.key).await.unwrap();
    let permit = p.feed_permission(&round.input.key).await.unwrap();
    assert!(support::encoding::permission_has_digest_refs(&permit));
    // 内部事件能写 ≠ 公开帧能完整还原: 快照文档本身可读…
    assert!(p.feed_snapshot(&round.input.key).await.is_ok());
    // …但内容损坏后公开还原失败: digest 不符是有界读取错误, 无静默截断。
    p.corrupt_result(&round.input.key).await.unwrap();
    let Err(StoreError::Invalid { code }) = p.get(&round.input.key).await else {
        panic!("expected archive mismatch") };
    assert_eq!(code, "archive_mismatch");
    assert!(p.sse_frame(&round.input.key, &profile).await.is_err());
    assert!(p.round_frame(&round.input.key, 0, &profile).await.is_err());
    // 恢复: 重传正确内容后公开帧完整还原。
    p.repair_result(&round.input.key, original.clone()).await.unwrap();
    let restored = p.sse_frame(&round.input.key, &profile).await.unwrap();
    assert_eq!(restored.value["result"], original);
    // 引用缺失 (blob 丢失): 有界依赖错误, 不伪造结果。
    p.lose_archive(&round.input.key).await.unwrap();
    assert!(matches!(p.get(&round.input.key).await,
        Err(StoreError::NotApplied { code }) if code == "archive_missing"));
    // 恢复: 重归档后完整读回。
    p.archive_value(&round.input.key, original.clone()).await.unwrap();
    let again = p.round_frame(&round.input.key, 0, &profile).await.unwrap();
    assert_eq!(again.value["result"], original);
}

#[tokio::test]
async fn archive_reference_stays_pinned_until_append_and_publish_complete() {
    let p = Prototype::new().await;
    let profile = small_profile(256, 1024, 1024 * 1024);
    let round = request("a", "pinned-round", 1);
    p.admit(&round).await.unwrap();
    p.archive_value(&round.input.key, artifacts_result(&chain_parts(64, 'x')).unwrap())
        .await.unwrap();
    p.finish_profiled(&round.input.key, &profile).await.unwrap();
    // 大产物引用在发布/重放完成前保持 pin: 过期清理拒绝物理删除。
    p.expire_tombstone(&round.input.key).await.unwrap();
    assert_not_applied(p.expire_cleanup(&round.input.key).await, "archive_pinned");
    p.append_feed(&round.input.key).await.unwrap();
    // append 完成 ≠ 发布完成: publish 之前仍 pin。
    assert_not_applied(p.expire_cleanup(&round.input.key).await, "archive_pinned");
    p.publish_snapshot(&round.input.key).await.unwrap();
    // 义务完成后 pin 释放, 清理闭环。
    p.expire_cleanup(&round.input.key).await.unwrap();
    assert!(p.get(&round.input.key).await.unwrap().is_none());
    assert_eq!(p.admit(&round).await.unwrap(), Admission::Expired);
}

#[tokio::test]
async fn oversized_intermediate_result_meets_the_full_stop_barrier() {
    let p = Prototype::new().await;
    let round = request("a", "barrier", 1);
    p.admit(&round).await.unwrap();
    let key = round.input.key.clone();
    // 兄弟 attempt 同时在途, 每个都有独立派发效果。
    p.dispatch_attempts(&key, &["a", "b"]).await.unwrap();
    // 超大 9 MiB 中间产物: 流式读取, 到 8 MiB+1 即止, 大对象从不完整载入。
    let inbound = PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES;
    let mut nine = ChunkSource::new(9 * 1024 * 1024);
    assert!(read_round_payload(&mut nine, inbound).is_err());
    assert_eq!(nine.read, inbound + 1);
    // 几 KB 候选最终产物本身可读, 但不能豁免大中间结果。
    let mut candidate = ChunkSource::new(2 * 1024);
    let small_final = read_round_payload(&mut candidate, inbound).unwrap();
    assert_eq!(small_final.len(), 2 * 1024);
    // 读取失败不是停止证明: 未核验的停止事实前, 失败收尾被屏障拒绝。
    assert_not_applied(p.fail_with_reason(&key, "oversized intermediate").await,
        "stop_unconfirmed");
    assert!(p.get(&key).await.unwrap().unwrap().terminal.is_none());
    // 未止的轮次阻塞下一轮受理。
    let depend = request("a", "depend", 2);
    assert_eq!(p.admit(&depend).await.unwrap(), Admission::Busy);
    // 错误身份的"停止事实"同样不是证明, 且不允许静默覆盖。
    p.corrupt_attempt_stop(&key, "a").await.unwrap();
    assert_not_applied(p.fail_with_reason(&key, "oversized intermediate").await,
        "stop_unconfirmed");
    let Err(StoreError::Invalid { code }) = p.stop_attempt(&key, "a").await else {
        panic!("expected stop identity conflict") };
    assert_eq!(code, "stop_identity");
    // 恢复: 重写正确的身份核验停止事实; 兄弟 b 未停仍阻止整轮失败终态。
    p.restore_attempt_stop(&key, "a").await.unwrap();
    assert_not_applied(p.fail_with_reason(&key, "oversized intermediate").await,
        "stop_unconfirmed");
    p.stop_attempt(&key, "b").await.unwrap();
    // 停止事实独立持久化并经身份核验, owner 交接后仍然有效。
    let q = p.takeover().await;
    q.fail_with_reason(&key, "oversized intermediate").await.unwrap();
    let fact_a = q.attempt_stop_fact(&key, "a").await.unwrap().unwrap();
    assert_eq!(fact_a, json!({"key": key, "attempt": "a"}));
    assert!(fact_a.get("result").is_none());
    // 有界失败事件以稳定身份收尾整轮; 失败记录有界且可正常读回 (不走损坏隔离)。
    let view = q.get(&key).await.unwrap().unwrap();
    let terminal = view.terminal.clone().unwrap();
    assert_eq!(terminal.status, TerminalStatus::Failed);
    assert_eq!(view.result, Some(Payload::Inline(
        json!({"failed": true, "reason": "oversized intermediate"}))));
    let permit = q.feed_permission(&key).await.unwrap();
    let events = permit["events"].as_array().unwrap().clone();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0]["kind"], "terminal");
    assert_eq!(events[0]["status"], "failed");
    assert_eq!(events[0]["id"], json!([terminal.commit_seq.0, 2]));
    // 超限字节不是停止证明也不进入产物: 无 artifact 事件, 无整轮交付效果。
    assert!(events.iter().all(|e| e["kind"] != "artifact"));
    assert!(q.deliveries().await.unwrap().is_empty());
    // 失败终态后: 依赖该节点的派发被拒, 下一轮可受理但从未消费超限结果。
    let Err(StoreError::Invalid { code }) = q.dispatch(&key).await else {
        panic!("expected terminal dispatch refusal") };
    assert_eq!(code, "terminal_dispatch");
    assert!(matches!(q.admit(&depend).await.unwrap(), Admission::New { .. }));
    q.append_feed(&key).await.unwrap();
    q.publish_snapshot(&key).await.unwrap();
    let failed_frame = q.sse_frame(&key, &RoundBudgetProfile::v1()).await.unwrap();
    assert_eq!(failed_frame.value["status"], "failed");
    assert_eq!(failed_frame.value["result"], json!({"failed": true, "reason": "oversized intermediate"}));
}
