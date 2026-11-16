use super::*;
use std::{any::Any, collections::VecDeque, fmt::Debug, sync::Mutex};

struct Step {
    method: &'static str,
    arguments: String,
    reply: Box<dyn Any + Send>,
}

/// Exact call ordering and argument checks keep a passing harness from silently
/// testing another identity, dropping a proof, or omitting a required read/write.
#[derive(Default)]
pub struct Script(Mutex<VecDeque<Step>>);

impl Script {
    pub fn expect<R: Send + 'static>(
        self,
        method: &'static str,
        args: impl Debug,
        reply: R,
    ) -> Self {
        self.0.lock().unwrap().push_back(Step {
            method,
            arguments: format!("{args:?}"),
            reply: Box::new(reply),
        });
        self
    }

    pub fn call<R: Send + 'static>(&self, method: &str, args: impl Debug) -> R {
        let step = self
            .0
            .lock()
            .unwrap()
            .pop_front()
            .expect("unexpected extra call");
        assert_eq!(method, step.method, "wrong contract method");
        assert_eq!(
            format!("{args:?}"),
            step.arguments,
            "wrong contract arguments"
        );
        *step
            .reply
            .downcast::<R>()
            .unwrap_or_else(|_| panic!("wrong scripted reply type for {method}"))
    }

    pub fn replace<R: Send + 'static>(&self, index: usize, reply: R) {
        self.0.lock().unwrap()[index].reply = Box::new(reply);
    }

    pub fn remaining(&self, expected: usize) {
        assert_eq!(
            self.0.lock().unwrap().len(),
            expected,
            "missing contract calls"
        );
    }

    pub fn done(&self) {
        self.remaining(0);
    }
}

pub fn ok<T>(value: T) -> Result<T, StoreError> {
    Ok(value)
}

pub async fn rejects(future: impl Future<Output = ContractResult>, message: &str) {
    let panic = std::panic::AssertUnwindSafe(future)
        .catch_unwind()
        .await
        .expect_err("invalid provider behavior was accepted by the harness");
    let actual = panic
        .downcast_ref::<String>()
        .map(String::as_str)
        .or_else(|| panic.downcast_ref::<&str>().copied())
        .unwrap_or("non-string panic");
    assert!(actual.contains(message), "unexpected rejection: {actual}");
}
