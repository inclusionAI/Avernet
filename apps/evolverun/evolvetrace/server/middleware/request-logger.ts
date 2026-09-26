import type { Request, Response, NextFunction } from "express";

export function requestLogger(req: Request, res: Response, next: NextFunction): void {
  const start = Date.now();
  const method = req.method;
  const path = req.originalUrl || req.path;

  res.on("finish", () => {
    const duration = Date.now() - start;
    const status = res.statusCode;
    const msg = `${method} ${path} ${status} ${duration}ms`;
    if (status >= 400) {
      console.error(`[evolvetrace] ${msg}`);
    } else {
      console.log(`[evolvetrace] ${msg}`);
    }
  });

  next();
}
