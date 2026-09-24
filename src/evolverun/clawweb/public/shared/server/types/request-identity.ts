export type RequestIdentity = {
  authorization?: string;
  cookie?: string;
  referer?: string;
  origin?: string;
  userId: string;
};
