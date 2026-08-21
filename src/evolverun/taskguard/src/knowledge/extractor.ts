/**
 * Keyword extraction for knowledge base search queries.
 *
 * Handles both CJK and Latin text with stop-word filtering,
 * CJK bigram/trigram tokenization, and frequency-based ranking.
 *
 * Adapted from ClawMind's extractor.ts for ClawFlow.
 */

/** English stop words commonly found in workflow prompts. */
const EN_STOP_WORDS = new Set([
  "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
  "of", "with", "by", "from", "is", "it", "as", "be", "was", "were",
  "been", "are", "have", "has", "had", "do", "does", "did", "will",
  "would", "could", "should", "may", "might", "can", "shall", "not",
  "no", "nor", "so", "if", "then", "than", "too", "very", "just",
  "about", "above", "after", "again", "all", "also", "any", "because",
  "before", "between", "both", "each", "few", "more", "most", "other",
  "our", "out", "over", "own", "same", "some", "such", "that", "this",
  "these", "those", "through", "under", "until", "up", "what", "when",
  "where", "which", "while", "who", "whom", "how", "why",
]);

/** Chinese stop words commonly found in workflow prompts. */
const ZH_STOP_WORDS = new Set([
  "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
  "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会",
  "着", "没有", "看", "好", "自己", "这", "他", "她", "它", "们",
  "那", "些", "什么", "如何", "怎么", "可以", "但是", "因为", "所以",
  "如果", "虽然", "已经", "还是", "或者", "而且", "这个", "那个",
  "哪", "谁", "多", "少", "几", "第", "等", "之", "与", "及",
  "其", "被", "把", "让", "给", "从", "向", "对", "为",
]);

/** Combined set for quick lookup. */
const ALL_STOP_WORDS = new Set([...EN_STOP_WORDS, ...ZH_STOP_WORDS]);

/** Check if a character is CJK. */
function isCJK(ch: string): boolean {
  const code = ch.codePointAt(0)!;
  return (
    (code >= 0x4e00 && code <= 0x9fff) ||   // CJK Unified Ideographs
    (code >= 0x3400 && code <= 0x4dbf) ||   // CJK Extension A
    (code >= 0xf900 && code <= 0xfaff)       // CJK Compatibility Ideographs
  );
}

/** Check if a character is CJK, Hiragana, or Katakana. */
function isCJKOrKana(ch: string): boolean {
  const code = ch.codePointAt(0)!;
  return (
    isCJK(ch) ||
    (code >= 0x3040 && code <= 0x309f) ||   // Hiragana
    (code >= 0x30a0 && code <= 0x30ff)       // Katakana
  );
}

/** Generate CJK bigrams and trigrams from a string of CJK characters. */
function cjkNgrams(text: string): string[] {
  const ngrams: string[] = [];
  // Collect CJK character sequences
  const segments = text.match(/[一-鿿㐀-䶿぀-ゟ゠-ヿ]+/g) ?? [];

  for (const seg of segments) {
    // Bigrams
    for (let i = 0; i < seg.length - 1; i++) {
      const bg = seg.slice(i, i + 2);
      if (!isStopBigram(bg)) ngrams.push(bg);
    }
    // Trigrams
    for (let i = 0; i < seg.length - 2; i++) {
      ngrams.push(seg.slice(i, i + 3));
    }
    // Whole word if all CJK and length >= 2 and not a stop word
    if (seg.length >= 2 && seg.length <= 6 && [...seg].every(isCJK)) {
      if (!ALL_STOP_WORDS.has(seg)) ngrams.push(seg);
    }
  }

  return ngrams;
}

/** Check if a bigram is a stop bigram (both characters are stop words). */
function isStopBigram(bg: string): boolean {
  const chars = [...bg];
  return chars.every((ch) => ZH_STOP_WORDS.has(ch));
}

/**
 * Extract search keywords from text.
 *
 * Handles both CJK and Latin text:
 * - CJK text generates bigrams, trigrams, and whole-word tokens
 * - Latin text splits on whitespace/punctuation, filters stop words
 * - Results are ranked by frequency (most common first)
 *
 * @param text Input text to extract keywords from
 * @param maxKeywords Maximum number of keywords to return (default: 10)
 * @returns Array of keyword strings, sorted by frequency descending
 */
export function extractKeywords(text: string, maxKeywords: number = 10): string[] {
  const lower = text.toLowerCase();

  // Split into CJK and non-CJK segments
  const freq = new Map<string, number>();

  // Process CJK ngrams
  const ngrams = cjkNgrams(lower);
  for (const ng of ngrams) {
    freq.set(ng, (freq.get(ng) ?? 0) + 1);
  }

  // Process non-CJK tokens (Latin words, numbers, etc.)
  const nonCjkTokens = lower.split(/[一-鿿㐀-䶿぀-ゟ゠-ヿ\s,，。、！？；：""''（）【】《》\-\—\–\/\\|@#$%^&*+=~`<>{}\[\]]+/);
  for (const token of nonCjkTokens) {
    const cleaned = token.replace(/[^a-z0-9]/g, "").trim();
    if (cleaned.length > 1 && !EN_STOP_WORDS.has(cleaned)) {
      freq.set(cleaned, (freq.get(cleaned) ?? 0) + 1);
    }
  }

  // Sort by frequency descending, then alphabetically for tie-breaking
  const sorted = [...freq.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, maxKeywords)
    .map(([word]) => word);

  return sorted;
}