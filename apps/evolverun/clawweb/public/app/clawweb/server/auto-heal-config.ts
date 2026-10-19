// Compatibility entry point; shared consumers should import the workflow config module.
export {
  resolveAutoHealGitConfigFromDb,
  resolveAutoHealGitSettings,
  type AutoHealDbGitConfig,
} from "@avernet/workflow/server/config/auto-heal-git-config";
