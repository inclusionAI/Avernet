//! Pure validation for `ServiceSpec` patch operations.
//!
//! Phase 1 only provides the validation function; Phase 2 will wire it into
//! the HTTP `PATCH /groups/{id}/settings` route. The function enforces two
//! rules:
//!
//! 1. `callback_config` is immutable in this version — any patch attempting
//!    to change it returns `Conflict`.
//! 2. `timeout_seconds` and `max_concurrency` may only change when no service
//!    invocation session is running for the group; otherwise `Conflict`.

use bcs_service_api::ServiceSpec;

/// Errors returned by [`validate_service_spec_patch`].
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ServiceSpecPatchError {
    /// Patch attempts to mutate `callback_config`, which is immutable.
    #[error("immutable field: service_spec.callback_config")]
    CallbackConfigImmutable,

    /// Patch attempts to mutate `timeout_seconds` or `max_concurrency` while
    /// service invocation sessions are still running.
    #[error(
        "cannot modify service_spec.{{timeout_seconds,max_concurrency}} while {0} service session(s) are running"
    )]
    RouteFieldsLocked(u64),
}

/// Validates a proposed `ServiceSpec` patch against the current value and the
/// running service-invocation session count.
///
/// Returns `Ok(())` if the patch is allowed, `Err(...)` otherwise.
///
/// Semantics:
/// - `current = None`, `new_spec = None` → no-op, allowed.
/// - `current = None`, `new_spec = Some(spec)` → installation, allowed (count is irrelevant).
/// - `current = Some(spec)`, `new_spec = None` → removal, allowed if count == 0.
/// - `current = Some(a)`, `new_spec = Some(b)` → field-by-field check:
///   - `a.callback_config != b.callback_config` → `CallbackConfigImmutable`.
///   - `a.timeout_seconds != b.timeout_seconds || a.max_concurrency != b.max_concurrency`
///     AND `count > 0` → `RouteFieldsLocked`.
pub fn validate_service_spec_patch(
    current: Option<&ServiceSpec>,
    new_spec: Option<&ServiceSpec>,
    running_service_count: u64,
) -> Result<(), ServiceSpecPatchError> {
    match (current, new_spec) {
        (None, None) => Ok(()),
        (None, Some(_)) => Ok(()), // installation: route fields don't need lock
        (Some(_), None) => {
            // removal — treat as locking the route fields if anything is running
            if running_service_count > 0 {
                Err(ServiceSpecPatchError::RouteFieldsLocked(running_service_count))
            } else {
                Ok(())
            }
        }
        (Some(a), Some(b)) => {
            if !callback_configs_equal(&a.callback_config, &b.callback_config) {
                return Err(ServiceSpecPatchError::CallbackConfigImmutable);
            }
            let route_fields_changed =
                a.timeout_seconds != b.timeout_seconds || a.max_concurrency != b.max_concurrency;
            if route_fields_changed && running_service_count > 0 {
                return Err(ServiceSpecPatchError::RouteFieldsLocked(running_service_count));
            }
            Ok(())
        }
    }
}

/// Compare two `Option<CallbackConfig>` values for equality.
///
/// `CallbackConfig` does not derive `PartialEq`, so we compare via JSON
/// serialization. The wire representation is the contract.
fn callback_configs_equal(
    a: &Option<bcs_service_api::CallbackConfig>,
    b: &Option<bcs_service_api::CallbackConfig>,
) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(x), Some(y)) => {
            let xj = serde_json::to_string(x).ok();
            let yj = serde_json::to_string(y).ok();
            xj == yj
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::{CallbackChannelConfig, CallbackConfig};
    use bcs_service_api::ServiceSpec;

    fn spec(
        timeout: Option<i32>,
        concurrency: Option<i32>,
        callback: Option<CallbackConfig>,
    ) -> ServiceSpec {
        ServiceSpec {
            callback_config: callback,
            timeout_seconds: timeout,
            max_concurrency: concurrency,
        }
    }

    #[test]
    fn no_op_none_to_none() {
        assert!(validate_service_spec_patch(None, None, 0).is_ok());
        assert!(validate_service_spec_patch(None, None, 5).is_ok());
    }

    #[test]
    fn install_none_to_some_always_ok() {
        let s = spec(Some(60), Some(8), None);
        assert!(validate_service_spec_patch(None, Some(&s), 0).is_ok());
        assert!(validate_service_spec_patch(None, Some(&s), 100).is_ok());
    }

    #[test]
    fn remove_some_to_none_blocked_when_running() {
        let s = spec(Some(60), Some(8), None);
        assert!(validate_service_spec_patch(Some(&s), None, 0).is_ok());
        assert!(matches!(
            validate_service_spec_patch(Some(&s), None, 1),
            Err(ServiceSpecPatchError::RouteFieldsLocked(1))
        ));
    }

    #[test]
    fn timeout_change_blocked_when_running() {
        let a = spec(Some(60), Some(8), None);
        let b = spec(Some(120), Some(8), None);
        assert!(validate_service_spec_patch(Some(&a), Some(&b), 0).is_ok());
        assert!(matches!(
            validate_service_spec_patch(Some(&a), Some(&b), 1),
            Err(ServiceSpecPatchError::RouteFieldsLocked(1))
        ));
    }

    #[test]
    fn max_concurrency_change_blocked_when_running() {
        let a = spec(Some(60), Some(8), None);
        let b = spec(Some(60), Some(16), None);
        assert!(validate_service_spec_patch(Some(&a), Some(&b), 0).is_ok());
        assert!(matches!(
            validate_service_spec_patch(Some(&a), Some(&b), 3),
            Err(ServiceSpecPatchError::RouteFieldsLocked(3))
        ));
    }

    #[test]
    fn callback_config_immutable_regardless_of_running_count() {
        let cfg = CallbackConfig {
            channels: vec![CallbackChannelConfig::AntDing {
                access_key_id: "k".into(),
                access_key_secret: "s".into(),
                robot_code: "r".into(),
                user_id: None,
                open_conversation_id: None,
            }],
        };
        let a = spec(Some(60), Some(8), None);
        let b = spec(Some(60), Some(8), Some(cfg));
        // running=0 — STILL blocked: callback_config is immutable regardless
        assert!(matches!(
            validate_service_spec_patch(Some(&a), Some(&b), 0),
            Err(ServiceSpecPatchError::CallbackConfigImmutable)
        ));
        // running=5 — also blocked
        let cfg2 = CallbackConfig {
            channels: vec![CallbackChannelConfig::AntDing {
                access_key_id: "k".into(),
                access_key_secret: "s".into(),
                robot_code: "r".into(),
                user_id: None,
                open_conversation_id: None,
            }],
        };
        let a2 = spec(Some(60), Some(8), None);
        let b2 = spec(Some(60), Some(8), Some(cfg2));
        assert!(matches!(
            validate_service_spec_patch(Some(&a2), Some(&b2), 5),
            Err(ServiceSpecPatchError::CallbackConfigImmutable)
        ));
    }

    #[test]
    fn no_change_ok_even_when_running() {
        let a = spec(Some(60), Some(8), None);
        let b = spec(Some(60), Some(8), None);
        assert!(validate_service_spec_patch(Some(&a), Some(&b), 100).is_ok());
    }
}
