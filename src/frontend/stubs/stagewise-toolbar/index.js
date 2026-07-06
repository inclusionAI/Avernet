// MIT no-op stub for @stagewise/toolbar (upstream is AGPL-3.0-only).
// The umi stagewise feature is gated on `enableBy: config` and this project
// does not set a `stagewise` config key, so initToolbar is never invoked.
// The no-op keeps the build safe even if the feature is ever enabled.
function initToolbar() {}

module.exports = { initToolbar: initToolbar, default: { initToolbar: initToolbar } };
