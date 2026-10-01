import type { Request, Response, NextFunction } from "express";

/** Wrap an async route handler so rejected promises are forwarded to Express error handling. */
export function asyncHandler<T extends Request = Request>(
  fn: (req: T, res: Response, next: NextFunction) => Promise<unknown>,
) {
  return (req: T, res: Response, next: NextFunction): void => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}
