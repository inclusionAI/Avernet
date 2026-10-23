import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { AgentControlPlaneEnvironment } from "../../services/repair/common/task-control-plane.js";

export type RepairBackgroundJob = "agent_execution" | "recovery_maintenance" | "recovery_reporting";
export type RepairScanLease = { environment: AgentControlPlaneEnvironment; job: RepairBackgroundJob; owner: string };

/** Short atomic statements; a scan never holds a database transaction over external I/O. */
export class RepairSchedulerRepository {
  private readonly clock: string;
  constructor(private readonly db: IDatabase) {
    this.clock = db.dbType === "sqlite" ? "unixepoch()" : "UNIX_TIMESTAMP()";
  }
  async claim(lease: RepairScanLease): Promise<boolean> {
    const conflict = this.db.dbType === "sqlite" ? "ON CONFLICT DO NOTHING" : "ON DUPLICATE KEY UPDATE job_name = job_name";
    await this.db.exec(`INSERT INTO ce_repair_scheduler_leases (environment, job_name)
      VALUES (?, ?) ${conflict}`, [lease.environment, lease.job]);
    const changed = await this.db.exec(`UPDATE ce_repair_scheduler_leases
      SET lease_owner = ?, lease_expires_at = ${this.clock} + 60, next_run_at = ${this.clock} + 20
      WHERE environment = ? AND job_name = ? AND lease_expires_at <= ${this.clock} AND next_run_at <= ${this.clock}`,
      [lease.owner, lease.environment, lease.job]);
    return changed.affectedRows === 1;
  }
  async renew(lease: RepairScanLease): Promise<boolean> {
    const changed = await this.db.exec(`UPDATE ce_repair_scheduler_leases SET lease_expires_at = ${this.clock} + 60
      WHERE environment = ? AND job_name = ? AND lease_owner = ? AND lease_expires_at > ${this.clock}`,
      [lease.environment, lease.job, lease.owner]);
    if (changed.affectedRows === 1) return true;
    // MySQL can report zero changed rows when two renewals land in the same second.
    return this.isHeld(lease);
  }
  async isHeld(lease: RepairScanLease): Promise<boolean> {
    const rows = await this.db.query(`SELECT id FROM ce_repair_scheduler_leases
      WHERE environment = ? AND job_name = ? AND lease_owner = ? AND lease_expires_at > ${this.clock}`,
      [lease.environment, lease.job, lease.owner]);
    return rows.length === 1;
  }
  async complete(lease: RepairScanLease): Promise<void> {
    await this.db.exec(`UPDATE ce_repair_scheduler_leases
      SET lease_owner = '', lease_expires_at = 0
      WHERE environment = ? AND job_name = ? AND lease_owner = ? AND lease_expires_at > ${this.clock}`,
      [lease.environment, lease.job, lease.owner]);
  }
}
