/**
 * Async callback subsystem — public API.
 *
 * Re-exports from the callback sub-modules for convenient consumption.
 *
 * @module callback
 */

export {
  type CallbackTokenStatus,
  type CallbackTokenRecord,
  type CallbackTokenRegistry,
  createCallbackTokenRegistry,
  generateCallbackToken,
  parseTimeoutToEpoch,
} from "./token-registry.js";

export {
  type AuthResult,
  verifyHmacSignature,
  authenticateCallback,
} from "./auth.js";

export {
  type PayloadValidationResult,
  validateCallbackPayload,
  buildCallbackResult,
} from "./payload-validator.js";

export {
  startCallbackTimeoutPoller,
  stopCallbackTimeoutPoller,
  captureCallbackPollerDeps,
} from "./timeout-poller.js";

export { createCallbackRouter, type CallbackRouterDeps } from "./router.js";