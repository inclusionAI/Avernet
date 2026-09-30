import { Router } from "express";

export function createAuthRouter(): Router {
  const router = Router();

  router.get("/me", (req, res) => {
    res.json({
      userId: req.userId ?? null,
      isAdmin: req.isAdmin ?? false,
    });
  });

  return router;
}
