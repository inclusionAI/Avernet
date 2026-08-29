import { CollaborationSquareError } from './collaborationSquareError';
import type { CollaborationSquareGateway } from './collaborationSquareGateway';

function unsupported(): never {
  throw new CollaborationSquareError('unsupported', '协作广场服务暂不可用');
}

export class UnsupportedCollaborationSquareAdapter implements CollaborationSquareGateway {
  async listBots() {
    return unsupported();
  }
  async discoverBots() {
    return unsupported();
  }
  async getBotProfile() {
    return unsupported();
  }
  async requestBotFriendship() {
    return unsupported();
  }
  async openBotConversation() {
    return unsupported();
  }
  async listGroups() {
    return unsupported();
  }
  async listGroupMembers() {
    return unsupported();
  }
  async createGroupSession() {
    return unsupported();
  }
}
