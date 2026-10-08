export interface BotLink {
  id: string;
  name: string;
  url: string;
  link_type: 'yuque' | 'dima' | 'antcode';
  access_modes?: Array<'READ' | 'WRITE'>;
}
export type BotLinkInput = Omit<BotLink, 'id'>;
