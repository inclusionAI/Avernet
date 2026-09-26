import { strict as assert } from 'node:assert';
import { createCronExecutionId, createManualRunId } from '../src/cron/run-id.js';

// Parity check: these two formats must stay byte-for-byte identical to
// openclaw (src/cron/run-id.ts createCronExecutionId; service/ops.ts manual:).
describe('cron run-id', () => {
  it('createCronExecutionId builds cron:<jobId>:<startedAt>', () => {
    assert.equal(createCronExecutionId('job-1', 1700000000000), 'cron:job-1:1700000000000');
  });

  it('createManualRunId builds manual:<jobId>:<startedAt>:<attempt>', () => {
    assert.equal(createManualRunId('job-1', 1700000000000, 1), 'manual:job-1:1700000000000:1');
    assert.equal(createManualRunId('job-2', 42, 7), 'manual:job-2:42:7');
  });
});
