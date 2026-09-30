/**
 * Stub WorkflowDeployHistoryRepository for Evolvetrace.
 */
import type { IDatabase } from "../db.js";

export type WorkflowDeployHistoryRow = {
  id: number;
  pack_id: string;
  workflow_id: string;
  version: number;
  deploy_number: number;
  tag_name: string;
  action: string;
  from_deploy_number: number | null;
  spec_json: string;
  note: string | null;
  bot_id: string | null;
  owner_id: string | null;
  operator: string | null;
  status: string;
  gmt_create: number;
  gmt_modified: number;
};

export type WorkflowDeployHistoryInsertInput = {
  packId: string;
  workflowId: string;
  deployNumber: number;
  version: number;
  tagName: string;
  action: string;
  fromDeployNumber?: number;
  specJson: string;
  botId?: string | null;
  ownerId?: string | null;
  note?: string;
};

export class WorkflowDeployHistoryRepository {
  constructor(private db: IDatabase) {}

  async listHistory(_workflowId: string, _limit: number): Promise<WorkflowDeployHistoryRow[]> {
    return [];
  }

  async findByVersion(_workflowId: string, _version: number): Promise<WorkflowDeployHistoryRow | null> {
    return null;
  }

  async findByDeployNumber(_workflowId: string, _deployNumber: number): Promise<WorkflowDeployHistoryRow | null> {
    return null;
  }

  async getLatestVersion(_workflowId: string): Promise<number> {
    return 0;
  }

  async getLatestDeploy(_packId: string, _workflowId: string): Promise<WorkflowDeployHistoryRow | null> {
    return null;
  }

  async getMaxDeployNumber(_packId: string, _workflowId: string): Promise<number> {
    return 0;
  }

  async insert(_data: WorkflowDeployHistoryInsertInput): Promise<WorkflowDeployHistoryRow> {
    // Stub: return a dummy row
    const now = Math.floor(Date.now() / 1000);
    return {
      id: 0,
      pack_id: _data.packId,
      workflow_id: _data.workflowId,
      version: _data.version,
      deploy_number: _data.deployNumber,
      tag_name: _data.tagName,
      action: _data.action,
      from_deploy_number: _data.fromDeployNumber ?? null,
      spec_json: _data.specJson,
      note: _data.note ?? null,
      bot_id: _data.botId ?? null,
      owner_id: _data.ownerId ?? null,
      operator: null,
      status: "active",
      gmt_create: now,
      gmt_modified: now,
    };
  }

  async findByWorkflowAndDeployNumber(_workflowId: string, _deployNumber: number): Promise<WorkflowDeployHistoryRow | null> {
    return null;
  }

  async findByVersionDeployOrEdit(_workflowId: string, _version: number): Promise<WorkflowDeployHistoryRow | null> {
    return null;
  }
}
