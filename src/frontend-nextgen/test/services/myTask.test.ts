import { runtimeStatusesFromProductFilter } from '@/services/myTask';
import { describe, expect, it } from '@jest/globals';

describe('myTask status query mapping', () => {
  it('已完成状态同时查询 DONE 和 SUCCESS', () => {
    expect(runtimeStatusesFromProductFilter('DONE')).toEqual(['DONE', 'SUCCESS']);
  });

  it('执行中状态使用逗号分隔的多状态', () => {
    expect(runtimeStatusesFromProductFilter('EXECUTING')).toEqual(['PLANNING', 'RUNNING']);
  });
});
