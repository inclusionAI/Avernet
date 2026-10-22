//! 复现（不修改生产代码）：人工审批耗时超过 idle 窗口后，`run_turn` 的
//! 滑动 idle 看门狗会在收到审批结果的那一瞬间杀掉本应继续的 turn。
//!
//! 原始问题：`run_turn` 的
//! 主循环只在 `cli.next_line()` 分支里刷新 `idle_deadline`。而
//! `CcMap::ControlRequest` 分支 `handle_control_request(...).await` 期间，
//! 引擎在等人类点“允许”（stdout 静默是协议要求的正常状态），返回后循环
//! 顶部的 `sleep_until(idle_deadline)` 拿到的还是控制请求到达时刻的旧
//! deadline —— 早已过期 —— 看门狗立刻触发，turn 以
//! `stream idle for …` 被误杀。人类感受：“我只是回答得慢了一点，
//! 明明已经点了允许，任务却失败了。”
//!
//! 复用 `mock_cc_approval.py`（Bash 场景）：先发 `can_use_tool` 控制请求，
//! 等 stdin 收到 `control_response` 后再输出 `result`。
//!
//! 时间线（人工审批耗时为 idle 窗口的 3 倍）：
//!
//! ```text
//! t=0      引擎发出 control_request；测试收到 interaction/requested（弹窗）
//! t=9s     人类点“允许”（registry.resolve → allow-once）
//!          ── 期望：引擎继续跑，turn 以 result "approved" 正常收尾
//!          ── 实际（bug）：idle_deadline 仍停在 t=3s，立刻判 idle，杀 turn
//! ```
//!
//! 对照组 `fast_approval_completes` 证明唯一的变量是人的延迟。

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use bcs_bridge_core::engine::claude_code::ClaudeCode;
use bcs_bridge_core::engine::{Engine, SessionObserver, TurnError, TurnOutcome, TurnRequest};
use bcs_bridge_core::interaction::{InteractionRegistry, ResolveOutcome};
use bcs_protocol::stream::{InteractionPhase, StreamEvent};
use serde_json::json;
use tokio_util::sync::CancellationToken;

// idle 看门狗也覆盖 Python 子进程启动，400ms 在繁忙 CI 上会在弹窗前超时。
// 保留启动余量，同时让慢审批显著超过窗口，继续覆盖旧 deadline 的回归。
const IDLE_WINDOW: Duration = Duration::from_secs(3);
const TURN_TIMEOUT: Duration = Duration::from_secs(30);

struct NoopObserver;

#[async_trait::async_trait]
impl SessionObserver for NoopObserver {
    async fn established(&self, _engine_session_id: &str) -> Result<(), String> {
        Ok(())
    }
}


/// 跑一轮“弹窗审批”turn：等待 `interaction/requested`，模拟人在
/// `human_latency` 后点“允许”，然后等 turn 收尾并返回其结局。
async fn drive_approval(human_latency: Duration, idle_window: Duration) -> Result<TurnOutcome, TurnError> {
    let script = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/mock_cc_approval.py");
    let engine = ClaudeCode::new(PathBuf::from(script), vec![]);
    let mut req = TurnRequest::new("r-slow", "hi", "/tmp", Arc::new(NoopObserver));
    req.idle_timeout = Some(idle_window);
    let registry: InteractionRegistry = req.interactions.clone();
    let (tx, mut rx) = tokio::sync::mpsc::channel::<StreamEvent>(64);
    let turn = {
        let engine = engine;
        tokio::spawn(async move { engine.run_turn(req, tx, CancellationToken::new()).await })
    };

    // 人类看到“是否允许执行？”弹窗的时刻。
    let iid = loop {
        let Some(event) = rx.recv().await else {
            let result = turn.await.expect("run_turn task panicked before interaction/requested");
            panic!("stream ended before interaction/requested: {result:?}");
        };
        match event {
            StreamEvent::Interaction(ev) if ev.phase == InteractionPhase::Requested => {
                assert_eq!(ev.kind, bcs_protocol::stream::InteractionKind::Exec);
                break ev.interaction_id;
            }
            _ => continue,
        }
    };
    // 人类去查资料，回来点了“允许”。
    tokio::time::sleep(human_latency).await;
    assert!(
        matches!(
            registry.resolve(&iid, "repro-key", json!({"decision":"allow-once"})),
            ResolveOutcome::Delivered
        ),
        "审批决定必须送达 parked driver"
    );
    // 收尾：run_turn 结束（无论成败）都会 drop events 发送端。
    while rx.recv().await.is_some() {}
    turn.await.expect("run_turn task panicked")
}

#[tokio::test]
async fn fast_approval_completes() {
    // 对照组：人几乎立刻点“允许”（50ms << idle 窗口）。必须成功。
    let outcome = tokio::time::timeout(
        TURN_TIMEOUT,
        drive_approval(Duration::from_millis(50), IDLE_WINDOW),
    )
    .await
    .expect("turn must not wedge")
    .expect("fast approval must resume the turn");
    assert_eq!(outcome.final_text.as_deref(), Some("approved"));
}

#[tokio::test]
async fn slow_approval_must_not_be_killed_by_idle_watchdog() {
    // 复现主体：人 3× idle 窗口后才点“允许”。
    // 允许已经点了、引擎本该继续干活。
    // 该测试断言【正确】行为；在 bug 未修复时它会失败，失败输出里的
    // `stream idle for 3s` 就是复现证据。
    let result = tokio::time::timeout(
        TURN_TIMEOUT,
        drive_approval(IDLE_WINDOW * 3, IDLE_WINDOW),
    )
    .await
    .expect("turn must not wedge");
    let outcome = result.unwrap_or_else(|err| {
        panic!(
            "已点击“允许”的 turn 被误杀：人工审批 3× idle 窗口后 \
             回复被接受，却因旧 idle_deadline 立刻被判 idle —— {err}"
        )
    });
    assert_eq!(outcome.final_text.as_deref(), Some("approved"));
}
