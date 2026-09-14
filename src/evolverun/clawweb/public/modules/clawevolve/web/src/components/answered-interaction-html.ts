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
      + '</head><body>' + template.innerHTML
      + '<style>input:disabled,textarea:disabled,select:disabled{opacity:1;-webkit-text-fill-color:currentColor}textarea{field-sizing:content}button:disabled,input[type=submit]:disabled,input[type=reset]:disabled{cursor:default}</style></body></html>',
    remainingAnswer: Object.keys(values).length ? (Object.keys(unmapped).length ? unmapped : null) : answer,
  }
}
