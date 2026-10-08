import { Router, raw } from "express";
import type { FilesystemObjectStore } from "./filesystem-object-store.js";

export function createFilesystemObjectStoreRouter(store: FilesystemObjectStore): Router {
  const router = Router();

  router.put(
    "/api/singlebox/artifacts/:token",
    raw({ type: "*/*", limit: "10mb" }),
    async (request, response) => {
      try {
        const key = store.resolveSignedRequest(String(request.params.token), "PUT");
        const result = await store.putObject(
          key,
          Buffer.isBuffer(request.body) ? request.body : Buffer.from(request.body ?? ""),
          request.header("content-type") ?? "application/octet-stream",
        );
        response.set("ETag", result.etag).status(204).end();
      } catch (error) {
        response.status(400).json({ error: error instanceof Error ? error.message : String(error) });
      }
    },
  );

  router.get("/api/singlebox/artifacts/:token", async (request, response) => {
    try {
      const key = store.resolveSignedRequest(String(request.params.token), "GET");
      const object = await store.getObject(key);
      if (object.etag) response.set("ETag", object.etag);
      response.type(object.contentType ?? "application/octet-stream").send(object.content);
    } catch (error) {
      response.status(400).json({ error: error instanceof Error ? error.message : String(error) });
    }
  });

  return router;
}
