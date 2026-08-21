/**
 * OpenClaw-specific type definitions.
 *
 * Centralises the PluginApi type so that both index.ts and openclaw-adapter.ts
 * share a single source of truth.
 *
 * @module platform/openclaw-types
 */

import type { PluginApi as SdkPluginApi } from "openclaw/plugin-sdk/plugin-entry";

/**
 * PluginApi type from the OpenClaw Plugin SDK.
 * Re-exported here for centralised access across the codebase.
 */
export type PluginApi = SdkPluginApi;
