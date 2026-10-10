import { CommunityApiGateway } from './communityApiGateway';
import { CommunityService } from './communityService';

export const communityService = new CommunityService(new CommunityApiGateway());
export type { CommunityGateway } from './communityGateway';
export { CommunityService } from './communityService';
