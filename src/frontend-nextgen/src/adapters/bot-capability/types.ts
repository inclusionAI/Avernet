export interface BotCapabilityProfile {
  canChat: boolean;
  canPublish: boolean;
  canSchedule: boolean;
  canConfigureResources: boolean;
  unsupportedReasons: Record<string, string>;
}
