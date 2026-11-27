/**
 * The engine addresses models as `provider/model`. A definition written from the
 * selector carries a bare catalogue name, so submission has to qualify it —
 * otherwise the engine falls back to a guessed provider.
 */
import { KNOWN_MODELS, normalizeModelId, resolveModel } from '@/domain/agentswarm/models';

describe('normalizeModelId', () => {
  // The bot channel sends the stored value straight to the Bot's session, so a
  // value this client invents would be one the Bot cannot resolve. Catalogue
  // ids are already provider-qualified; nothing here may add to them.
  it('stores every id verbatim, qualified or not', () => {
    expect(normalizeModelId('modelmng/6651a744')).toBe('modelmng/6651a744');
    expect(normalizeModelId('openai/gpt-5.3')).toBe('openai/gpt-5.3');
    expect(normalizeModelId('Kimi-K2.6')).toBe('Kimi-K2.6');
  });

  it('never invents a provider prefix', () => {
    for (const name of KNOWN_MODELS) {
      expect(normalizeModelId(name)).toBe(name);
    }
  });

  it('trims, and treats blank or absent as no choice', () => {
    expect(normalizeModelId('  openai/gpt-5.3  ')).toBe('openai/gpt-5.3');
    expect(normalizeModelId('')).toBeUndefined();
    expect(normalizeModelId('   ')).toBeUndefined();
    expect(normalizeModelId(null)).toBeUndefined();
    expect(normalizeModelId(undefined)).toBeUndefined();
  });
});

describe('resolveModel', () => {
  it('prefers the agent model over the run-level one', () => {
    expect(resolveModel('GLM-5', 'Kimi-K2.6')).toBe('GLM-5');
  });

  it('falls back to the run-level model, then to nothing', () => {
    expect(resolveModel(null, 'Kimi-K2.6')).toBe('Kimi-K2.6');
    expect(resolveModel(null, null)).toBeUndefined();
    expect(resolveModel('', '  ')).toBeUndefined();
  });
});