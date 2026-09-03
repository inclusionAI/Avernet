import {
  friendApprovalConfigsEqual,
  normalizePublicConfig,
  publicConfigsEqual,
  validateFriendApproval,
  validatePublicConfig,
} from '@/domain/collaborationPrivacy/policies';
import type {
  CollaborationBot,
  CollaborationPrivacyOverview,
  OrganizationSearchEntry,
} from '@/domain/collaborationPrivacy/types';
import type {
  CollaborationPrivacyGateway,
  DirectSettingCommand,
  FriendApprovalCommand,
  PublicationCommand,
} from './collaborationPrivacyGateway';
import { createCollaborationPrivacyRuntimeAdapter } from './collaborationPrivacyRuntimeAdapter';

export class CollaborationPrivacyService {
  private overview?: CollaborationPrivacyOverview;
  private readonly inFlightActions = new Set<string>();
  constructor(private readonly gateway: CollaborationPrivacyGateway) {}

  async loadOverview(userId: string, signal?: AbortSignal) {
    this.overview = await this.gateway.loadOverview(userId, signal);
    return structuredClone(this.overview);
  }

  private requireBot(botId: string): CollaborationBot {
    const bot = this.overview?.bots.find((item) => item.id === botId);
    if (!bot) throw new Error('未找到目标 Bot');
    return bot;
  }

  private requireWritableBot(botId: string): CollaborationBot {
    const bot = this.requireBot(botId);
    if (!bot.joinedBcn) throw new Error('加入 BCN 后才能修改协作权限');
    return bot;
  }

  async refreshBot(botId: string, signal?: AbortSignal) {
    this.requireBot(botId);
    const actionKey = `refresh:${botId}`;
    if (this.inFlightActions.has(actionKey)) throw new Error('该 Bot 状态正在刷新，请勿重复操作');
    this.inFlightActions.add(actionKey);
    try {
      const refreshedBot = await this.gateway.refreshManagedBot(botId, signal);
      if (refreshedBot.id !== botId) throw new Error('Bot 详情接口返回了不匹配的 Bot');
      if (this.overview) {
        this.overview.bots = this.overview.bots.map((bot) => (bot.id === botId ? refreshedBot : bot));
      }
      return structuredClone(refreshedBot);
    } finally {
      this.inFlightActions.delete(actionKey);
    }
  }
  async syncDepartment(userId: string, signal?: AbortSignal) {
    const actionKey = 'syncDepartment';
    if (this.inFlightActions.has(actionKey)) throw new Error('用户部门信息正在同步，请勿重复操作');
    this.inFlightActions.add(actionKey);
    try {
      const identity = await this.gateway.syncDepartment(userId, signal);
      const previous = this.overview?.currentUser;
      const changed =
        !previous ||
        previous.displayName !== identity.displayName ||
        previous.employeeNumber !== identity.employeeNumber ||
        previous.departmentPath.join('\u0000') !== identity.departmentPath.join('\u0000');
      if (this.overview) this.overview.currentUser = identity;
      return { identity, changed };
    } finally {
      this.inFlightActions.delete(actionKey);
    }
  }

  async searchDepartments(keyword: string, signal?: AbortSignal): Promise<OrganizationSearchEntry[]> {
    const actionKey = 'searchDepartments';
    if (this.inFlightActions.has(actionKey)) throw new Error('部门搜索正在进行，请稍后再试');
    this.inFlightActions.add(actionKey);
    try {
      return await this.gateway.searchDepartments(keyword, signal);
    } finally {
      this.inFlightActions.delete(actionKey);
    }
  }

  async updateDirectSetting(command: DirectSettingCommand, signal?: AbortSignal) {
    const bot = this.requireWritableBot(command.botId);
    const actionKey = `direct:${command.botId}:${command.setting}`;
    if (this.inFlightActions.has(actionKey)) throw new Error('该设置正在提交，请勿重复操作');
    this.inFlightActions.add(actionKey);
    try {
      const value = await this.gateway.updateDirectSetting(command, signal);
      if (command.setting === 'collaborationStatus') bot.collaborationStatus = value as 'online' | 'hidden';
      else if (command.setting === 'profilePublic') bot.profilePublic = Boolean(value);
      else if (command.setting === 'taskClaimingEnabled') bot.taskClaimingEnabled = Boolean(value);
      else bot.dreamModelEnabled = Boolean(value);
      return structuredClone(bot);
    } finally {
      this.inFlightActions.delete(actionKey);
    }
  }

  async submitPublication(command: PublicationCommand, signal?: AbortSignal) {
    const bot = this.requireWritableBot(command.botId);
    if (bot.pendingPublications[command.audience]) throw new Error('该公开范围已有待审批变更');
    const config = validatePublicConfig(normalizePublicConfig(command.config));
    if (publicConfigsEqual(bot.publication[command.audience], config)) throw new Error('配置未发生变化，无需提交工单');
    const actionKey = `publication:${command.botId}:${command.audience}`;
    if (this.inFlightActions.has(actionKey)) throw new Error('该公开范围正在提交，请勿重复操作');
    this.inFlightActions.add(actionKey);
    try {
      const result = await this.gateway.submitPublication(
        { ...command, config, deptEntries: command.deptEntries },
        signal,
      );
      if (result.status === 'pending') {
        bot.pendingPublications[command.audience] = result.publication;
      } else {
        bot.publication[command.audience] = structuredClone(result.config);
        delete bot.pendingPublications[command.audience];
      }
      return structuredClone(bot);
    } finally {
      this.inFlightActions.delete(actionKey);
    }
  }

  async updateFriendApproval(command: FriendApprovalCommand, signal?: AbortSignal) {
    const bot = this.requireWritableBot(command.botId);
    if (bot.publication.user.scope === 'none' && bot.publication.bot.scope === 'none') {
      throw new Error('至少开放一种公开范围后才能修改好友审批策略');
    }
    const config = validateFriendApproval(command.config);
    if (friendApprovalConfigsEqual(bot.friendApproval, config)) throw new Error('配置未发生变化，无需保存');
    const actionKey = `friendApproval:${command.botId}`;
    if (this.inFlightActions.has(actionKey)) throw new Error('好友审批策略正在保存，请勿重复操作');
    this.inFlightActions.add(actionKey);
    try {
      bot.friendApproval = await this.gateway.updateFriendApproval({ ...command, config }, signal);
      return structuredClone(bot);
    } finally {
      this.inFlightActions.delete(actionKey);
    }
  }

  async enableTaskClaim(botId: string, signal?: AbortSignal) {
    this.requireWritableBot(botId);
    const actionKey = `direct:${botId}:taskClaimingEnabled`;
    if (this.inFlightActions.has(actionKey)) throw new Error('任务认领授权正在提交，请勿重复操作');
    this.inFlightActions.add(actionKey);
    try {
      const updated = await this.gateway.enableTaskClaim(botId, signal);
      if (this.overview) this.overview.bots = this.overview.bots.map((item) => (item.id === botId ? updated : item));
      return structuredClone(updated);
    } finally {
      this.inFlightActions.delete(actionKey);
    }
  }
  async disableTaskClaim(botId: string, signal?: AbortSignal) {
    this.requireWritableBot(botId);
    const actionKey = `direct:${botId}:taskClaimingEnabled`;
    if (this.inFlightActions.has(actionKey)) throw new Error('任务认领授权正在提交，请勿重复操作');
    this.inFlightActions.add(actionKey);
    try {
      const updated = await this.gateway.disableTaskClaim(botId, signal);
      if (this.overview) this.overview.bots = this.overview.bots.map((item) => (item.id === botId ? updated : item));
      return structuredClone(updated);
    } finally {
      this.inFlightActions.delete(actionKey);
    }
  }
}

export const collaborationPrivacyService = new CollaborationPrivacyService(createCollaborationPrivacyRuntimeAdapter());
