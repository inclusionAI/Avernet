// @ts-nocheck  // Tests: no test runner installed yet (vitest/jest). Kept for reference.
import { GitOperationQueue } from "../queue.js";

describe("GitOperationQueue", () => {
  test("executes operations in order", async () => {
    const queue = new GitOperationQueue();
    const order: number[] = [];
    await queue.enqueue(async () => { order.push(1); });
    await queue.enqueue(async () => { order.push(2); });
    await queue.enqueue(async () => { order.push(3); });
    expect(order).toEqual([1, 2, 3]);
  });

  test("failed operation does not block subsequent operations", async () => {
    const queue = new GitOperationQueue();
    const order: number[] = [];
    const p1 = queue.enqueue(async () => { throw new Error("boom"); });
    const p2 = queue.enqueue(async () => { order.push(2); });
    await expect(p1).rejects.toThrow("boom");
    await p2;
    expect(order).toEqual([2]);
  });

  test("concurrent enqueues are serialized", async () => {
    const queue = new GitOperationQueue();
    const order: number[] = [];
    const p1 = queue.enqueue(async () => {
      await new Promise((r) => setTimeout(r, 50));
      order.push(1);
    });
    const p2 = queue.enqueue(async () => {
      order.push(2);
    });
    await Promise.all([p1, p2]);
    expect(order).toEqual([1, 2]);
  });
});