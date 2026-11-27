/**
 * Model matching and storage, independent of the Select's interaction.
 *
 * A stored model is whatever the Bot's catalogue spelled, which is normally
 * provider-qualified (`provider/model`); older entries may hold a bare name.
 * Matching ignores the prefix so both show as the model they name, while the
 * stored value is never rewritten.
 */
import { normalizeModel } from '@/components/agentswarm/ModelSelector';
import { KNOWN_MODELS } from '@/domain/agentswarm/models';

/** The same normalisation the selector applies before matching a stored value. */
function bareName(value: string): string {
  return value.includes('/') ? (value.split('/').pop() ?? value) : value;
}

describe('a stored model is matched by its bare name', () => {
  it.each(KNOWN_MODELS)('%s is known both bare and qualified', (model) => {
    expect(KNOWN_MODELS.includes(bareName(model))).toBe(true);
    expect(KNOWN_MODELS.includes(bareName(`provider/${model}`))).toBe(true);
  });

  it('an unknown name stays custom under either spelling', () => {
    expect(KNOWN_MODELS.includes(bareName('some-new-model'))).toBe(false);
    expect(KNOWN_MODELS.includes(bareName('other/some-new-model'))).toBe(false);
  });
});

describe('normalizeModel stores the chosen id verbatim', () => {
  // The stored value goes to the Bot's session unchanged, so it has to be an id
  // that Bot already recognises. Anything this client adds would not be.
  it.each([
    ['modelmng/6651a744', 'modelmng/6651a744'],
    ['openai/gpt-5.3', 'openai/gpt-5.3'],
    ['Kimi-K2.6', 'Kimi-K2.6'],
    ['', null],
    ['   ', null],
    [null, null],
  ])('%j -> %j', (input, expected) => {
    expect(normalizeModel(input)).toBe(expected);
  });
});
