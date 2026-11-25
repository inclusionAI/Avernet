import { appendGovernanceEvent } from "../services/insight/governance-item.js";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { InsightImprovementRepository } from "./insight-improvement-repository.js";
import { appendManagementEvent, managementEvents, ManagementError, type ManagementActor, type ManagementCommand } from "../services/insight/management/contracts.js";
import type { ManagementChange, ManagementSnapshot, ManagementStore } from "../services/insight/management/service.js";

export class InsightManagementRepository implements ManagementStore {
  constructor(private readonly db: IDatabase) {}
  get(id: number) { return new InsightImprovementRepository(this.db).getDetailById(id); }
  list(input: Parameters<ManagementStore["list"]>[0]) {
    return new InsightImprovementRepository(this.db).listAdmin({ ...input, includeAll: true });
  }
  apply(id: number, command: ManagementCommand, actor: ManagementActor, fingerprint: string,
    decide: (item: ManagementSnapshot) => ManagementChange) {
    return this.db.transaction(async tx => {
      const repo = new InsightImprovementRepository(tx);
      const item = await repo.getDetailById(id);
      if (!item) throw new ManagementError("NOT_FOUND", "改进项不存在", 404);
      const previous = managementEvents(item.userGuidance).find(event => event.requestId === command.requestId);
      if (previous) {
        if (previous.fingerprint !== fingerprint || previous.actor.id !== actor.id || previous.actor.kind !== actor.kind) {
          throw new ManagementError("REQUEST_ID_CONFLICT", "requestId 已用于不同请求", 409);
        }
        return { improvement: item, receipt: previous, replayed: true };
      }
      if (item.version !== command.version) throw new ManagementError("VERSION_CONFLICT", "改进项已更新，请重新读取", 409);
      const change = decide(item);
      const guidance = command.action === "reject" ? appendGovernanceEvent(item.userGuidance, {
        title: "管理员驳回", values: [["原因", "OTHER"], ["说明", command.reason], ["时间", change.event.at], ["操作人", actor.id]],
      }) : item.userGuidance;
      const result = await tx.exec(`UPDATE insight_improvement_item
        SET status = ?, title = ?, source_type = ?, user_guidance = ?, version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ?`, [change.status, change.title, change.sourceType,
        appendManagementEvent(guidance, change.event), tx.dialect.now(), id, command.version]);
      if (result.affectedRows !== 1) throw new ManagementError("VERSION_CONFLICT", "改进项已更新，请重新读取", 409);
      const updated = await repo.getDetailById(id);
      if (!updated || updated.version !== change.event.afterVersion) throw new Error("Management readback failed");
      return { improvement: updated, receipt: change.event, replayed: false };
    });
  }
}
