/** COMMUNITY STUB: api-client.ts is internal-only. Corp extensions provide real implementation. */

export type ApiClientConfig = {
  baseUrl?: string;
  privateKeyB64?: string;
  [k: string]: any;
};

export type ApiResponse<T = any> = {
  ok: boolean;
  data?: T;
  status?: number;
  error?: string;
};

export class ApiClient {
  constructor(_config: ApiClientConfig | unknown) {}

  async get<T = any>(..._args: any[]): Promise<ApiResponse<T>> {
    return { ok: false } as any;
  }

  async post<T = any>(..._args: any[]): Promise<ApiResponse<T>> {
    return { ok: false } as any;
  }

  async put<T = any>(..._args: any[]): Promise<{ ok: boolean; data?: T; status?: number; error?: string }> {
    return { ok: false } as any;
  }

  async delete<T = any>(..._args: any[]): Promise<{ ok: boolean; data?: T; status?: number; error?: string }> {
    return { ok: false } as any;
  }

  [k: string]: any;
}
