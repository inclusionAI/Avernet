export interface ParticipantSummaryDto {
  actor_id: string;
  actor_kind: 'human' | 'bot';
  name: string | null;
  avatar_url: string | null;
}
// 新服务字段必需；只有消费旧响应时允许缺头像。
export type ParticipantSummaryReadDto = Omit<ParticipantSummaryDto, 'avatar_url'> & {
  avatar_url?: string | null;
};
