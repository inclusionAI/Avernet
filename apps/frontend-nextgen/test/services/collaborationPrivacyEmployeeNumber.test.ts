import { normalizeEmployeeNumber } from '@/utils/employeeNumber';
import { describe, expect, it } from '@jest/globals';

describe('collaboration privacy employee number compatibility', () => {
  it.each([
    ['123456', '123456'],
    ['012345', '012345'],
    ['001234', '001234'],
    ['12345', '012345'],
    ['1234', '001234'],
    ['WB123456', 'WB123456'],
    [' wb123456 ', 'wb123456'],
    ['1234567', '1234567'],
  ])('normalizes %s to %s', (input, expected) => {
    expect(normalizeEmployeeNumber(input)).toBe(expected);
  });
});
