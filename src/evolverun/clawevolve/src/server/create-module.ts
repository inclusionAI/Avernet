import { Router } from "express";

import {
  createTaskDefinitionsResponse,
  type EvolveTaskDefinitionsOptions,
} from "./routes/evolve.js";

/**
 * Builds the public Clawevolve HTTP module without starting another process.
 *
 * A host may extend the public task definitions with private definitions and
 * command defaults. Those values are supplied at composition time and are not
 * stored in this package.
 */
export function createClawevolveModule(
  options: EvolveTaskDefinitionsOptions = {},
): Router {
  const router = Router();
  router.get("/task-definitions", (_request, response) => {
    response.json(createTaskDefinitionsResponse(options));
  });
  return router;
}
