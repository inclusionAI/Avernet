// Mirror of the backend name validator (``validate_bot_name`` in
// ``bot_service.py``): ``^[\w一-鿿 \-()+]+$`` with ``re.UNICODE`` and a 32-char
// length cap. Keeping the FE charset/length in sync with the server so a name
// the server rejects is not accepted here either — copy/create otherwise
// persist the manifest and AgentPass authorization first, then fail the
// async ``create_bot`` task on this check, leaving a half-built bot whose
// ``ac_bots`` row never lands (detail endpoint 404 forever).

export const BOT_NAME_MAX_LEN = 32;

// ``一-鿿`` = CJK Unified Ideographs (``一-鿿`` in the backend source),
// written as escapes to survive any source transcoding.
const BOT_NAME_ALLOWED_RE = /^[\w一-鿿 ()\-+]+$/u;

/** True if the name contains a character the backend name validator rejects. */
export function botNameHasInvalidCharacter(name: string): boolean {
  return !BOT_NAME_ALLOWED_RE.test(name);
}
