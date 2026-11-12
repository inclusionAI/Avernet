use super::*;
use crate::{Context, NetworkTracer, with_context, with_provider};

struct Provider;
impl NetworkTracer for Provider {
    fn start(&self, _: &crate::RequestInfo<'_>, _: &crate::Carrier, _: crate::ParentContext<'_>) -> crate::StartedTrace {
        panic!("reading log identity must never start a trace")
    }
    fn log_correlation(&self, context: &Context) -> Option<LogCorrelation> {
        context.downcast_ref::<LogCorrelation>().cloned()
    }
}

fn ids(trace: &str) -> LogCorrelation { LogCorrelation::new("network_trace", trace, "network_span", "0.2").unwrap() }
fn context(trace: &str) -> Option<Context> { Some(Arc::new(ids(trace))) }

#[test]
fn pair_is_bounded_atomic_and_cannot_overwrite_standard_fields() {
    for name in ["", "message", "timestamp", "log_class", "request_id", "trace_id", "a.b", "1id", "a\nid"] {
        assert!(LogCorrelation::new(name, "valid", "network_span", "0").is_none());
    }
    for value in ["", "bad\nvalue", "bad=value", "bad value", "中文"] {
        assert!(LogCorrelation::new("network_trace", value, "network_span", "0").is_none());
        assert!(LogCorrelation::new("network_trace", "valid", "network_span", value).is_none());
    }
    assert!(LogCorrelation::new("same", "valid", "same", "0").is_none());
    assert!(LogCorrelation::new("network_trace", &"a".repeat(257), "network_span", "0").is_none());
    assert_eq!(ids("valid").fields(), [("network_trace", "valid"), ("network_span", "0.2")]);
}

#[tokio::test]
async fn snapshot_is_logs_only_and_suppression_and_foreign_context_win() {
    assert!(current_log_correlation().is_none());
    let captured = with_provider(Some(Arc::new(Provider)), with_context(context("parent"), async {
        assert_eq!(current_log_correlation(), Some(ids("parent")));
        in_current_log_context(async {
            assert!(crate::current_context().is_none());
            assert_eq!(current_log_correlation(), Some(ids("parent")));
            with_provider(None, async { assert!(current_log_correlation().is_none()); }).await;
            with_provider(Some(Arc::new(Provider)), with_context(Some(Arc::new(42)), async {
                assert!(current_log_correlation().is_none());
            })).await;
            with_provider(Some(Arc::new(Provider)), with_context(context("child"), async {
                assert_eq!(current_log_correlation(), Some(ids("child")));
            })).await;
            assert_eq!(current_log_correlation(), Some(ids("parent")));
        })
    })).await;
    tokio::spawn(captured).await.unwrap();
    assert!(current_log_correlation().is_none());
}

#[tokio::test]
async fn concurrent_tasks_and_nested_scopes_do_not_share_identity() {
    let run = |trace: &'static str| async move {
        with_provider(Some(Arc::new(Provider)), with_context(context(trace), async move {
            for _ in 0..100 {
                tokio::task::yield_now().await;
                assert_eq!(current_log_correlation(), Some(ids(trace)));
                with_log_correlation(Some(ids("guard")), || {
                    assert_eq!(current_log_correlation(), Some(ids("guard")));
                    with_log_correlation(None, || assert!(current_log_correlation().is_none()));
                    assert_eq!(current_log_correlation(), Some(ids("guard")));
                });
            }
            assert_eq!(current_log_correlation(), Some(ids(trace)));
        })).await;
    };
    tokio::join!(run("first"), run("second"));
    assert!(current_log_correlation().is_none());
}

#[tokio::test]
async fn aborted_future_can_emit_its_captured_identity_after_scope_exit() {
    struct Guard(Option<LogCorrelation>, Arc<std::sync::Mutex<Option<LogCorrelation>>>);
    impl Drop for Guard {
        fn drop(&mut self) {
            with_log_correlation(self.0.clone(), || {
                *self.1.lock().unwrap() = current_log_correlation();
            });
        }
    }
    let seen = Arc::new(std::sync::Mutex::new(None));
    let (ready, started) = tokio::sync::oneshot::channel();
    let output = seen.clone();
    let task = tokio::spawn(with_provider(Some(Arc::new(Provider)), with_context(context("cancelled"), async move {
        let _guard = Guard(current_log_correlation(), output);
        ready.send(()).unwrap();
        std::future::pending::<()>().await;
    })));
    started.await.unwrap();
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    assert_eq!(*seen.lock().unwrap(), Some(ids("cancelled")));
    assert!(current_log_correlation().is_none());
}

#[test]
fn panic_restores_override() {
    let result = std::panic::catch_unwind(|| with_log_correlation(Some(ids("panic")), || panic!("test")));
    assert!(result.is_err());
    assert!(current_log_correlation().is_none());
}
