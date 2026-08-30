import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';
import type { CollaborationDefinitionGraphPreview } from './collaborationGraphTypes';

/** YAML 校验诊断信息。 */
export interface ValidationDiagnostic {
  code: string;
  path: string;
  message: string;
  hint?: string;
}

/** 校验后的参与者槽位。 */
export interface ParticipantSlot {
  binding: string;
  display_name?: string;
  description?: string;
  required: boolean;
  assigned: boolean;
}

/** 校验摘要。 */
export interface ValidationSummary {
  participants: number;
  nodes: number;
  initial_nodes: string[];
  final_output_node?: string;
}

/** 校验响应数据（包裹在 envelope.data 中）。 */
export interface DefinitionValidationData {
  valid: boolean;
  errors?: ValidationDiagnostic[];
  warnings?: ValidationDiagnostic[];
  summary: ValidationSummary;
  participants?: ParticipantSlot[];
  graph?: CollaborationDefinitionGraphPreview;
}

const BASE = '/api/v1/collaboration/definitions';

/** 校验自定义协作 YAML：POST /api/v1/collaboration/definitions/validate。 */
export function validateDefinition(body: { definition_yaml: string }) {
  return backendRequest<BackendApiEnvelope<DefinitionValidationData>>(`${BASE}/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
  });
}
