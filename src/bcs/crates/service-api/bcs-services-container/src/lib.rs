//! BCS services container: production assembly and test-only Noop wiring.

pub mod services;
pub mod interceptor_chain;

#[cfg(any(test, feature = "test-support"))]
pub mod test_support;

pub use interceptor_chain::InterceptorChain;
pub use services::{BuilderError, Services, ServicesBuilder};
pub use bcs_service_api::SystemMessageService;

#[cfg(any(test, feature = "test-support"))]
pub mod testing {
    pub use crate::test_support::with_all_noop;
}
