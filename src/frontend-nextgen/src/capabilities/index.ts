import { defaultCapabilities } from './defaultCapabilities';
import type {
  AdminSections,
  AgentCodingInternalResources,
  AppCapabilities,
  BotEngineOption,
  BotSkillPickerSource,
  CapabilityResult,
  CapabilityStatus,
  CurrentUserContext,
  HelpLink,
  HumanIdentity,
  LoginStrategy,
  MetricsDashboardSpec,
  OpenSourceExperienceNoticeSpec,
  PersonalSpaceInitOptions,
  ProductBrand,
  ReleaseNotesCapability,
  ReleaseNotesData,
  SearchedUser,
  ShellVisibility,
  TaskClaimGrantStrategy,
  UserProfilePresentation,
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
  AdminSections,
  AgentCodingInternalResources,
  AppCapabilities,
  BotEngineOption,
  BotSkillPickerSource,
  CapabilityResult,
  CapabilityStatus,
  CurrentUserContext,
  HelpLink,
  HumanIdentity,
  LoginStrategy,
  MetricsDashboardSpec,
  OpenSourceExperienceNoticeSpec,
  PersonalSpaceInitOptions,
  ProductBrand,
  ReleaseNotesCapability,
  ReleaseNotesData,
  SearchedUser,
  ShellVisibility,
  TaskClaimGrantStrategy,
  UserProfilePresentation,
  UserSearchCapability,
};
