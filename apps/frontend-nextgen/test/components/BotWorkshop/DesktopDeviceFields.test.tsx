/** @jest-environment jsdom */
import { DesktopDeviceFields } from '@/components/BotWorkshop/CreateBotModal/DesktopDeviceFields';
import type { BotCreateInput } from '@/domain/botWorkshop';
import { localBotService } from '@/services/botWorkshop/localBotService';
import { render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
jest.mock('@/services/botWorkshop/localBotService');
function Form() {
  const [values, setValues] = useState<BotCreateInput>({
    scenario: 'local',
    name: 'Desk',
    description: '',
    engine: 'openclaw',
    spaceId: 'personal',
    ownership: 'personal',
    serviceMode: 'non-service',
    initialize: true,
  });
  return (
    <form>
      <DesktopDeviceFields values={values} setValues={setValues} />
      <output data-testid="path">{values.local?.mountPath}</output>
    </form>
  );
}
test('single device stays selected when Radix initializes inside a form', async () => {
  jest.mocked(localBotService.devices).mockResolvedValue([{ id: 'machine', name: 'Mock Mac', status: 'ACTIVE' }]);
  jest.mocked(localBotService.directory).mockResolvedValue('/workspace');
  render(<Form />);
  await waitFor(() => expect(screen.getByTestId('path').textContent).toBe('/workspace/Desk'));
  expect(localBotService.directory).toHaveBeenCalledWith('machine');
});
