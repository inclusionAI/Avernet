import {
  DSH_CLI_INSTALL_COMMAND,
  normalizeBcnEndpoint,
  renderDshConnectCommand,
  shellQuote,
} from './accessCommands';

describe('DSH access command rendering', () => {
  it('uses the published DSH CLI version in the install hint', () => {
    expect(DSH_CLI_INSTALL_COMMAND).toBe(
      'npm install --global @deepseek-ai/dsh@0.1.1-rc.2',
    );
  });

  it('quotes shell values before inserting endpoint and token', () => {
    expect(shellQuote("it's-valid")).toBe("'it'\"'\"'s-valid'");
    expect(
      renderDshConnectCommand(
        'BCN_ONBOARDING_TOKEN={token} bash --endpoint {endpoint}',
        'http://127.0.0.1:21000/api',
        "token-with-'quote",
      ),
    ).toBe(
      "BCN_ONBOARDING_TOKEN='token-with-'\"'\"'quote' bash --endpoint 'http://127.0.0.1:21000/api'",
    );
  });

  it('renders explicit placeholders when values are unavailable', () => {
    expect(
      renderDshConnectCommand('TOKEN={token} ENDPOINT={endpoint}', null, null),
    ).toBe("TOKEN='<YOUR_TOKEN>' ENDPOINT='<BCN_ENDPOINT>'");
  });

  it('accepts HTTP and HTTPS endpoints while rejecting unsupported schemes', () => {
    expect(normalizeBcnEndpoint('http://127.0.0.1:21000/')).toBe(
      'http://127.0.0.1:21000',
    );
    expect(normalizeBcnEndpoint('https://bcs.example.test/api/')).toBe(
      'https://bcs.example.test/api',
    );
    expect(normalizeBcnEndpoint('ftp://bcs.example.test')).toBeNull();
    expect(
      normalizeBcnEndpoint('http://user:pass@bcs.example.test'),
    ).toBeNull();
    expect(
      normalizeBcnEndpoint('http://bcs.example.test?token=secret'),
    ).toBeNull();
  });
});
