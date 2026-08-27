import type { CollaborationPrivacyGateway } from './collaborationPrivacyGateway';

function unsupported(): never {
  throw new Error('协作权限真实数据源尚未配置');
}

export const unsupportedCollaborationPrivacyAdapter: CollaborationPrivacyGateway = {
  async loadOverview() {
    return unsupported();
  },
  async syncDepartment() {
    return unsupported();
  },
  async searchDepartments() {
    return unsupported();
  },
  async updateDirectSetting() {
    return unsupported();
  },
  async submitPublication() {
    return unsupported();
  },
  async updateFriendApproval() {
    return unsupported();
  },
};
