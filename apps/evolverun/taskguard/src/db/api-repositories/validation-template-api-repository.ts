/**
 * ValidationTemplateApiRepository — HTTP client implementation of IValidationTemplateRepository.
 *
 * Best-effort no-op: the evolvetrace server has no HTTP endpoints for validation templates.
 * All methods log a warning and return safe defaults.
 */
import type { ApiClient } from "../api-client.js";
import type {
  IValidationTemplateRepository,
  ValidationTemplateRow,
} from "../repositories/types.js";

export class ValidationTemplateApiRepository implements IValidationTemplateRepository {
  constructor(private api: ApiClient) {}

  async findByTemplateId(templateId: string): Promise<ValidationTemplateRow | null> {
    void templateId;
    console.warn(
      "[ValidationTemplateApi] findByTemplateId is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async findEnabled(templateId: string): Promise<ValidationTemplateRow | null> {
    void templateId;
    console.warn(
      "[ValidationTemplateApi] findEnabled is not supported over HTTP API mode " +
        "(no server endpoint). Returning null.",
    );
    return null;
  }

  async listAll(enabledOnly?: boolean): Promise<ValidationTemplateRow[]> {
    void enabledOnly;
    console.warn(
      "[ValidationTemplateApi] listAll is not supported over HTTP API mode " +
        "(no server endpoint). Returning empty.",
    );
    return [];
  }
}