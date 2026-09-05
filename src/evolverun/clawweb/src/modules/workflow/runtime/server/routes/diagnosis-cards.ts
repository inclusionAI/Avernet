/**
 * createDiagnosisCardRouter — external admin route for the diagnosis_cards
 * (single-run analysis persistence) table. Exposes:
 *   GET /          — list diagnosis cards (filter by workflow / signature / outcome)
 *   GET /:id       — diagnosis card detail (with related repair_history rows)
 *
 * Mounted at /api/diagnosis-cards. Auth via the admin-auth middleware used by
 * other /api routes. Used by the Bigfish "进化 Tab · 诊断" sub-tab.
 *
 * Plan ref: self-evolution unified proposal §10.1 (G3 missing endpoint surface).
 */
import { Router, type Request, type Response } from "express";
import type { DiagnosisCardRepository, DiagnosisCardRow } from "../repositories/diagnosis-card-repository.js";
import type { RepairHistoryRepository } from "../repositories/repair-history-repository.js";
import { asyncHandler } from "../middleware/async-handler.js";

function safeParseJsonArray(raw: string | null): unknown[] {
  if (!raw) return [];
  try { const p = JSON.parse(raw); return Array.isArray(p) ? p : []; } catch { return []; }
}

export function createDiagnosisCardRouter(
  diagRepo: DiagnosisCardRepository,
  repairRepo: RepairHistoryRepository,
  authMiddleware: (req: Request, res: Response, next: (err?: unknown) => void) => void,
): Router {
  const r = Router();
  r.use(authMiddleware);

  // GET / — list cards with filters
  r.get("/", asyncHandler(async (req, res) => {
    const workflowId = typeof req.query.workflowId === "string" ? req.query.workflowId : null;
    const failureSignature = typeof req.query.failureSignature === "string" ? req.query.failureSignature : null;
    const outcome = typeof req.query.outcome === "string" ? req.query.outcome as DiagnosisCardRow["outcome"] : null;
    const limit = Number(req.query.limit ?? 20);
    const offset = Number(req.query.offset ?? 0);
    try {
      const result = await diagRepo.list({ workflowId, failureSignature, outcome, limit, offset });
      // Snapshots are heavy TEXT; the listing view returns lighter summary rows.
      const items = result.rows.map((row) => ({
        ...row,
        // Strip the giant snapshot/blob fields for the listing UI; they're fetched
        // via the detail endpoint (/api/diagnosis-cards/:id) when needed.
        input_snapshot: row.input_snapshot ? `<${Math.ceil(row.input_snapshot.length / 1024)}KB>` : null,
        output_snapshot: row.output_snapshot ? `<${Math.ceil(row.output_snapshot.length / 1024)}KB>` : null,
        step_traces_snapshot: row.step_traces_snapshot ? `<${Math.ceil(row.step_traces_snapshot.length / 1024)}KB>` : null,
        analysis_reasoning: row.analysis_reasoning ? `<${Math.ceil(row.analysis_reasoning.length / 1024)}KB>` : null,
      }));
      res.json({ items, total: result.total, limit, offset });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // GET /:id — full detail + related repair_history rows
  r.get("/:id", asyncHandler(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id) || id <= 0) return res.status(400).json({ error: "bad_request", message: "id must be a positive integer" });
    const card = await diagRepo.getById(id);
    if (!card) return res.status(404).json({ error: "not_found", message: `diagnosis card ${id} not found` });
    // Fetch related repair_history by the FK diagnosis_card_id —
    // NOT a failure_signature LIKE match. The same signature may surface on
    // multiple unrelated diagnosis cards (e.g. ambient tool errors that
    // recur across workflows); drilling all of them into this card's
    // "related repair_history" would misattribute the repair trail and break
    // §10.2 the repairability drill-down accuracy. Using the FK here returns
    // only rows that THIS card actually triggered, preserving the
    // audit-trail contract.
    const repairRows = await repairRepo.listByDiagnosisCardId(id, 50);
    res.json({
      ...card,
      input_snapshot_json: safeParseJsonArray(card.input_snapshot ?? null),
      output_snapshot_json: safeParseJsonArray(card.output_snapshot ?? null),
      step_traces_snapshot_json: safeParseJsonArray(card.step_traces_snapshot ?? null),
      related_repair_history: repairRows,
    });
  }));

  return r;
}