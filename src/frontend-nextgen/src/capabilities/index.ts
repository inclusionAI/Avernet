import { defaultCapabilities } from './defaultCapabilities';
import type {
  AppCapabilities,
  CapabilityResult,
  CapabilityStatus,
  CurrentUserContext,
  HelpLink,
  HumanIdentity,
  MetricsDashboardSpec,
  ReleaseNotesCapability,
  ReleaseNotesData,
  SearchedUser,
  UserSearchCapability,
} from './types';

let sealed = false;
let currentCapabilities: AppCapabilities = defaultCapabilities;

export function extendCapabilities(capabilities: Partial<AppCapabilities>): void {
  if (sealed) {
    throw new Error('capabilities 已冻结，不能在应用启动后继续扩展');
  }
  currentCapabilities = {
    ...currentCapabilities,
    ...capabilities,
  };
}

export function getCapabilities(): AppCapabilities {
  return currentCapabilities;
}

export function sealExtensions(): void {
  sealed = true;
}

export { defaultCapabilities };
export type {
  AppCapabilities,
  CapabilityResult,
  CapabilityStatus,
  CurrentUserContext,
  HelpLink,
  HumanIdentity,
  MetricsDashboardSpec,
  ReleaseNotesCapability,
  ReleaseNotesData,
  SearchedUser,
  UserSearchCapability,
};
