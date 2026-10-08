import { desktopCapabilityPolicy } from '@/services/botWorkshop/desktopCapabilityPolicy';
test('desktop restrictions retain the OpenClaw-only configuration exceptions', () => {
  expect(desktopCapabilityPolicy('local', 'openclaw')).toMatchObject({
    resourceWritable: false,
    channels: false,
    nodes: false,
    approval: false,
    sessionUploads: false,
    markdown: true,
    engineConfig: true,
    routines: true,
    screens: true,
  });
  expect(desktopCapabilityPolicy('local', 'hermes')).toMatchObject({
    markdown: false,
    engineConfig: false,
    routines: false,
    screens: false,
  });
  expect(desktopCapabilityPolicy('cloud', 'openclaw')).toMatchObject({
    resourceWritable: true,
    channels: true,
    sessionUploads: true,
  });
});
