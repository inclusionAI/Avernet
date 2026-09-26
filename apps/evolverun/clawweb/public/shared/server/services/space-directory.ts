import type { RequestIdentity } from "../types/request-identity.js";

/** The host remains authoritative for space identity and live membership. */
export type AccessibleSpace = {
  id: string;
  name: string;
  type: "PERSONAL" | "TEAM";
  role: "ADMIN" | "MEMBER";
};

export interface SpaceDirectory {
  listAccessibleSpaces(input: { identity: RequestIdentity }): Promise<AccessibleSpace[]>;
};
