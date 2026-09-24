export const platformInteractionFormStyle = `<style data-evolve-form-style="platform">
  *{box-sizing:border-box}
  html{color-scheme:light;background:#fff}
  body[data-evolve-form-theme="platform"]{margin:0;padding:4px;color:#1f2937;background:#fff;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;line-height:1.65}
  .evolve-form-shell{width:100%;max-width:100%;overflow:hidden}
  .evolve-form-shell form{margin:0;padding:20px;border:1px solid #e5e7eb;border-radius:14px;background:#fff}
  .evolve-form-shell h1{margin:0 0 8px;color:#111827;font-size:22px;line-height:1.35;font-weight:700}
  .evolve-form-shell h2{margin:24px 0 10px;padding-top:18px;border-top:1px solid #eef2f7;color:#111827;font-size:16px;line-height:1.45;font-weight:650}
  .evolve-form-shell h3{margin:18px 0 8px;color:#1f2937;font-size:15px;line-height:1.45;font-weight:650}
  .evolve-form-shell p{margin:8px 0;color:#4b5563}
  .evolve-form-shell form>p{padding:10px 12px;border-radius:9px;background:#f5f8ff;color:#42526b}
  .evolve-form-shell pre{max-height:360px;margin:8px 0 16px;overflow:auto;white-space:pre-wrap!important;overflow-wrap:anywhere;border:1px solid #e5e7eb;border-radius:10px;background:#f8fafc;padding:14px;color:#334155;font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace}
  .evolve-form-shell fieldset{min-width:0;margin:14px 0;padding:14px 16px 16px;border:1px solid #dfe5ee;border-radius:12px;background:#fbfcfe}
  .evolve-form-shell legend{padding:0 7px;color:#1d4ed8;font-size:13px;font-weight:700}
  .evolve-form-shell label{display:block;margin-top:10px;color:#374151;font-size:13px;font-weight:550}
  .evolve-form-shell input:not([type="checkbox"]):not([type="radio"]),.evolve-form-shell textarea,.evolve-form-shell select{display:block;width:100%;margin-top:6px;border:1px solid #d1d5db;border-radius:9px;background:#fff;padding:9px 11px;color:#1f2937;font:inherit;outline:none}
  .evolve-form-shell textarea{min-height:72px;resize:vertical}
  .evolve-form-shell input[type="checkbox"],.evolve-form-shell input[type="radio"]{width:16px;height:16px;margin:0 7px 0 0;vertical-align:-3px;accent-color:#2563eb}
  .evolve-form-shell button,.evolve-form-shell input[type="submit"]{margin-top:16px;border:0;border-radius:9px;background:#2563eb;padding:10px 16px;color:#fff;font:600 13px/1.2 inherit}
  .evolve-form-shell input:disabled,.evolve-form-shell textarea:disabled,.evolve-form-shell select:disabled{opacity:1;background:#f8fafc;color:#334155;-webkit-text-fill-color:#334155}
  .evolve-form-shell button:disabled,.evolve-form-shell input[type="submit"]:disabled{display:none}
</style>`

/** Keep the submitted form's layout, but render it as an inert answer snapshot. */
export function answeredInteractionHtml(content: string, answer: unknown) {
  const template = document.createElement('template')
  template.innerHTML = content
  const root = template.content
  const values = answer && typeof answer === 'object' && !Array.isArray(answer)
    ? answer as Record<string, unknown> : {}
  const restored = new Set<string>()
  const occurrences = new Map<string, number>()
  root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('input,textarea,select').forEach((field) => {
    const name = field.getAttribute('name') || ''
    const present = Object.prototype.hasOwnProperty.call(values, name)
    const value = present ? values[name] : null
    const items = (Array.isArray(value) ? value : value == null ? [] : [value]).map(String)
    const index = occurrences.get(name) || 0
    occurrences.set(name, index + 1)
    if (present && !(field.tagName === 'INPUT' && ['file', 'hidden', 'submit', 'button', 'reset', 'image'].includes((field as HTMLInputElement).type))) restored.add(name)
    if (field.tagName === 'SELECT') {
      Array.from((field as HTMLSelectElement).options).forEach((option) => option.toggleAttribute('selected', items.includes(option.value)))
      // A single select otherwise defaults to its first option even when no answer was submitted.
      if (!items.length || !Array.from((field as HTMLSelectElement).options).some((option) => items.includes(option.value))) {
        const blank = document.createElement('option')
        blank.textContent = '（未填写）'
        blank.setAttribute('selected', '')
        field.prepend(blank)
      }
    } else if (field.tagName === 'TEXTAREA') {
      field.textContent = items[index] ?? items[0] ?? ''
    } else {
      const input = field as HTMLInputElement
      if (input.type === 'checkbox' || input.type === 'radio') {
        input.toggleAttribute('checked', value === true || items.includes(input.value))
      } else if (!['file', 'hidden', 'submit', 'button', 'reset', 'image'].includes(input.type)) {
        input.setAttribute('value', items[index] ?? items[0] ?? '')
      }
    }
    field.setAttribute('disabled', '')
    field.setAttribute('readonly', '')
  })
  // No original scripts, navigation, embedded browsing contexts or submission in history.
  root.querySelectorAll('script,iframe,object,embed,base,meta,link').forEach((node) => node.remove())
  root.querySelectorAll('*').forEach((node) => {
    for (const attr of Array.from(node.attributes)) {
      if (/^on/i.test(attr.name) || ['href', 'xlink:href', 'action', 'formaction', 'target', 'autofocus'].includes(attr.name)) node.removeAttribute(attr.name)
    }
    if (node.hasAttribute('contenteditable')) node.setAttribute('contenteditable', 'false')
  })
  root.querySelectorAll('button,input[type="submit"],input[type="reset"],input[type="image"]').forEach((node) => node.setAttribute('disabled', ''))
  const unmapped = Object.fromEntries(Object.entries(values).filter(([name]) => !restored.has(name)))
  return {
    html: '<!doctype html><html><head><meta charset="utf-8">'
      + '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src data:; script-src \'none\'; form-action \'none\'; base-uri \'none\'">'
      + '</head><body data-evolve-form-theme="platform"><div class="evolve-form-shell">' + template.innerHTML
      + '</div>' + platformInteractionFormStyle + '</body></html>',
    remainingAnswer: Object.keys(values).length ? (Object.keys(unmapped).length ? unmapped : null) : answer,
  }
}
