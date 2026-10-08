//!
//! Note: workspace lints set `unsafe_code = "deny"`. Rust 2024 marks
//! `std::env::set_var` / `remove_var` as unsafe (not thread-safe). We
//! follow the repo convention seen in `bcs-cli` / `bcs-http-auth`:
//! per-block `#[allow(unsafe_code)]` rather than file-level.

use bcs_config::{RuntimeEnv, resolve_env};

#[allow(unsafe_code)]
fn set_env(key: &str, val: &str) {
    // SAFETY: see module note; tests run serially.
    unsafe { std::env::set_var(key, val) }
}

#[allow(unsafe_code)]
fn unset_env(key: &str) {
    // SAFETY: same as set_env.
    unsafe { std::env::remove_var(key) }
}

/// Single env-resolution test that runs all priority/normalization scenarios serially.
///
/// Env vars are process-global, so multiple `#[test]` functions cannot
/// reliably set/unset them in parallel. Combining into one test removes
/// flaky race conditions.
#[test]
fn resolve_env_handles_all_known_priorities() {
    let backup = (
        std::env::var("SERVER_ENV").ok(),
        std::env::var("REAL_SERVER_ENV").ok(),
        std::env::var("ALIPAY_APP_ENV").ok(),
    );

    // 1. No env var -> Dev
    unset_env("SERVER_ENV");
    unset_env("REAL_SERVER_ENV");
    unset_env("ALIPAY_APP_ENV");
    assert_eq!(resolve_env(), RuntimeEnv::Dev);

    // 2. SERVER_ENV=prod -> Prod
    set_env("SERVER_ENV", "prod");
    assert_eq!(resolve_env(), RuntimeEnv::Prod);

    // 3. SERVER_ENV takes priority over ALIPAY_APP_ENV
    set_env("SERVER_ENV", "prod");
    set_env("ALIPAY_APP_ENV", "pre");
    assert_eq!(resolve_env(), RuntimeEnv::Prod);

    // 4. prepub normalizes to Pre
    unset_env("SERVER_ENV");
    unset_env("ALIPAY_APP_ENV");
    set_env("SERVER_ENV", "prepub");
    assert_eq!(resolve_env(), RuntimeEnv::Pre);

    // Restore previous environment
    unset_env("SERVER_ENV");
    unset_env("REAL_SERVER_ENV");
    unset_env("ALIPAY_APP_ENV");
    if let Some(v) = backup.0 {
        set_env("SERVER_ENV", &v);
    }
    if let Some(v) = backup.1 {
        set_env("REAL_SERVER_ENV", &v);
    }
    if let Some(v) = backup.2 {
        set_env("ALIPAY_APP_ENV", &v);
    }
}
