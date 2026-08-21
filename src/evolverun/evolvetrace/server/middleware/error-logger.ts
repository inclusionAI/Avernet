import type { Request, Response, NextFunction } from "express";

export function errorLogger(err: unknown, req: Request, res: Response, _next: NextFunction): void {
  const method = req.method;
  const path = req.originalUrl || req.path;
  const message = err instanceof Error ? err.message : String(err);
  console.error(`[evolvetrace] ERROR ${method} ${path} - ${message}`);

  if (!res.headersSent) {
    res.status(500).json({ error: "Internal Server Error" });
  }
}
