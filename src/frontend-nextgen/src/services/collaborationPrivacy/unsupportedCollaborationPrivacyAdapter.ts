import type { CollaborationPrivacyGateway } from './collaborationPrivacyGateway';

function unsupported(): never {
  throw new Error('协作权限真实数据源尚未配置');
}

export const unsupportedCollaborationPrivacyAdapter: CollaborationPrivacyGateway = {
  async loadOverview(_userId: string) {
    void _userId;
    return unsupported();
  },
  async refreshManagedBot() {
    return unsupported();
  },
  async syncDepartment(_userId: string) {
    void _userId;
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
  async enableTaskClaim() {
    return unsupported();
  },
  async disableTaskClaim() {
    return unsupported();
  },
};
