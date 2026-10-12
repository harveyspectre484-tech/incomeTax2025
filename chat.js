const STORAGE_KEY = 'legal-rag-threads'
const SUGGESTIONS = [
  'What deduction is allowed for provision for bad debts of a scheduled bank?',
  'Explain Section 36(3)(a)(i).',
  'What is a demerged company under Section 2(34)?',
]

const $ = (id) => document.getElementById(id)
const shell = document.querySelector('.app-shell')
const els = {
  threadList: $('threadList'), chatContent: $('chatContent'), chatScroll: $('chatScroll'),
  question: $('questionInput'), send: $('sendButton'), evidence: $('evidenceContent'),
  connection: $('connection'), connectionLabel: $('connectionLabel'),
  banner: $('systemBanner'), bannerText: $('bannerText'), filterCount: $('filterCount'),
  filterLabel: $('activeFiltersLabel'), dialog: $('filterDialog'), overlay: $('overlay'),
}

function node(tag, className, content) {
  const element = document.createElement(tag)
  if (className) element.className = className
  if (content !== undefined && content !== null) element.textContent = String(content)
  return element
}

function button(className, text, onClick, label) {
  const element = node('button', className, text)
  element.type = 'button'
  if (label) element.setAttribute('aria-label', label)
  element.addEventListener('click', onClick)
  return element
}

function makeThread() {
  return { id: crypto.randomUUID(), title: 'New research', messages: [], createdAt: Date.now() }
}

function savedThreads() {
  try {
    const value = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null')
    if (Array.isArray(value) && value.length && value.every((item) =>
      typeof item.id === 'string' && Array.isArray(item.messages))) return value
  } catch { /* Session storage may be unavailable. */ }
  return [makeThread()]
}

const state = {
  threads: savedThreads(), activeId: null, activeMessageId: null, activeSourceId: null,
  health: null, healthError: false, sending: false,
  filters: { chapter: '', section: '', unit_type: '', jurisdiction: 'India' },
}
state.activeId = state.threads[0].id

function persist() {
  try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state.threads)) } catch { /* Keep in-memory history. */ }
}

function activeThread() {
  return state.threads.find((item) => item.id === state.activeId) || state.threads[0]
}

function activeAnswer() {
  const answers = activeThread().messages.filter((item) => item.role === 'assistant')
  return answers.find((item) => item.id === state.activeMessageId) || answers.at(-1)
}

function timeLabel(timestamp) {
  return new Intl.DateTimeFormat('en-IN', { hour: 'numeric', minute: '2-digit' }).format(timestamp)
}

function refreshComposer() {
  els.send.disabled = !els.question.value.trim() || state.sending || state.health?.status !== 'configured'
  const count = Object.entries(state.filters).filter(([key, value]) => value && !(key === 'jurisdiction' && value === 'India')).length
  els.filterCount.hidden = count === 0
  els.filterCount.textContent = count
  els.filterLabel.textContent = count ? `· ${count} filters` : ''
}

function renderStatus() {
  const health = state.health
  let label = 'Connecting'
  if (state.healthError) label = 'API disconnected'
  else if (health?.status === 'configured') label = health.model_loaded ? 'Ready' : 'Ready to load'
  else if (health?.status === 'setup_required') label = 'Setup required'
  els.connectionLabel.textContent = label
  els.connection.classList.toggle('ready', health?.status === 'configured')
  els.connection.classList.toggle('error', state.healthError)
  els.banner.hidden = !state.healthError && health?.status !== 'setup_required'
  if (state.healthError) els.bannerText.textContent = 'The local chat API is offline.'
  else if (health?.status === 'setup_required') {
    els.bannerText.textContent = `Set ${health.missing_configuration.join(' and ')} in the Python API server environment.`
  }
  refreshComposer()
}

async function checkHealth() {
  try {
    const response = await fetch('/api/health')
    if (!response.ok) throw new Error('API unavailable')
    state.health = await response.json()
    state.healthError = false
  } catch {
    state.health = null
    state.healthError = true
  }
  renderStatus()
}

function renderThreads() {
  els.threadList.replaceChildren()
  for (const thread of state.threads) {
    const row = node('div', `thread-row${thread.id === state.activeId ? ' active' : ''}`)
    const select = button('thread-select', null, () => {
      state.activeId = thread.id
      state.activeMessageId = null
      state.activeSourceId = null
      closeSidebar()
      render()
    })
    select.append(node('span', 'thread-icon', '▤'), node('span', 'thread-title', thread.title))
    const remove = button('delete-thread', '×', () => deleteThread(thread.id), `Delete ${thread.title}`)
    remove.title = 'Delete conversation'
    row.append(select, remove)
    els.threadList.append(row)
  }
}

function newThread() {
  const thread = makeThread()
  state.threads.unshift(thread)
  state.activeId = thread.id
  state.activeMessageId = null
  state.activeSourceId = null
  els.question.value = ''
  closeSidebar()
  persist()
  render()
  els.question.focus()
}

function deleteThread(id) {
  state.threads = state.threads.filter((item) => item.id !== id)
  if (!state.threads.length) state.threads = [makeThread()]
  if (state.activeId === id) state.activeId = state.threads[0].id
  state.activeMessageId = null
  state.activeSourceId = null
  persist()
  render()
}

function appendMessage(threadId, message) {
  const thread = state.threads.find((item) => item.id === threadId)
  if (!thread) return
  if (!thread.messages.length && message.role === 'user') {
    const title = message.text.replace(/\s+/g, ' ').trim()
    thread.title = title.length > 42 ? `${title.slice(0, 42)}...` : title
  }
  thread.messages.push(message)
  persist()
  render()
}

function sourceButton(source, messageId) {
  const label = source.citation || source.id
  const element = button('source-button', label, () => {
    state.activeMessageId = messageId
    state.activeSourceId = source.id
    openEvidence()
    renderEvidence()
  })
  element.title = `Inspect ${label}`
  return element
}

function renderAssistant(message) {
  const result = message.result || {}
  const wrapper = node('article', 'message assistant-message')
  wrapper.append(node('div', 'assistant-avatar', '§'))
  const body = node('div', 'message-body')
  const heading = node('div', 'message-heading')
  heading.append(node('strong', '', 'Act Research'), node('span', 'message-time', timeLabel(message.time)))
  body.append(heading)

  if (result.answer) {
    const answer = node('div', 'answer-text')
    for (const part of result.answer.split(/\n+/)) if (part.trim()) answer.append(node('p', '', part))
    body.append(answer)
    body.append(node('p', 'answer-caution', 'AI-generated answer. Verify it against the cited provisions.'))
  } else {
    const insufficient = node('div', 'insufficient')
    insufficient.append(node('span', '', '!'))
    const details = node('div')
    details.append(node('strong', '', 'Insufficient evidence'))
    for (const item of result.missing_evidence || []) details.append(node('p', '', item.reason || 'Required evidence is unavailable.'))
    insufficient.append(details)
    body.append(insufficient)
  }

  const sources = result.sources || []
  const citedIds = new Set((result.claims || []).flatMap((claim) => claim.evidence_ids || []))
  const cited = sources.filter((source) => citedIds.has(source.id))
  const visible = cited.length ? cited : sources.slice(0, 4)
  if (visible.length) {
    const section = node('div', 'inline-sources')
    section.append(node('div', 'inline-sources-title', cited.length ? 'CITED PROVISIONS' : 'RELATED PROVISIONS'))
    const links = node('div', 'source-buttons')
    for (const source of visible) links.append(sourceButton(source, message.id))
    section.append(links)
    body.append(section)
  }
  const footer = node('div', 'message-foot', `${sources.length} evidence units · ${Math.max(1, Math.round((result.elapsed_ms || 0) / 1000))}s`)
  if (result.tracing_enabled) footer.append(node('span', '', '· Trace recorded'))
  body.append(footer)
  wrapper.append(body)
  return wrapper
}

function renderMessages() {
  els.chatContent.replaceChildren()
  const thread = activeThread()
  if (!thread.messages.length) {
    const empty = node('div', 'empty-chat')
    empty.append(node('div', 'empty-mark', '§'), node('div', 'empty-kicker', 'INCOME-TAX ACT, 2025'), node('h1', '', 'Ask the Act'))
    const suggestions = node('div', 'suggestions')
    for (const suggestion of SUGGESTIONS) {
      const item = button('suggestion', null, () => { els.question.value = suggestion; refreshComposer(); els.question.focus() })
      item.append(node('span', '', suggestion), node('span', '', '→'))
      suggestions.append(item)
    }
    empty.append(suggestions)
    els.chatContent.append(empty)
    return
  }

  const list = node('div', 'messages')
  for (const message of thread.messages) {
    if (message.role === 'user') {
      const wrapper = node('div', 'message user-message')
      wrapper.append(node('div', 'user-bubble', message.text), node('span', 'message-time', timeLabel(message.time)))
      list.append(wrapper)
    } else if (message.role === 'error') {
      const error = node('div', 'request-error', message.text)
      error.setAttribute('role', 'alert')
      list.append(error)
    } else if (message.role === 'assistant') list.append(renderAssistant(message))
  }
  if (state.sending) {
    const working = node('div', 'working')
    working.setAttribute('role', 'status')
    working.append(node('span', 'working-dot'), node('span', '', 'Reviewing provisions...'))
    list.append(working)
  }
  els.chatContent.append(list)
  requestAnimationFrame(() => { els.chatScroll.scrollTop = els.chatScroll.scrollHeight })
}

function renderEvidence() {
  els.evidence.replaceChildren()
  const message = activeAnswer()
  const sources = message?.result?.sources || []
  if (!sources.length) {
    els.evidence.append(node('div', 'evidence-empty', 'No evidence selected'))
    return
  }
  const citedIds = new Set((message.result.claims || []).flatMap((claim) => claim.evidence_ids || []))
  const active = sources.find((source) => source.id === state.activeSourceId) || sources[0]
  els.evidence.append(node('div', 'evidence-summary', `${sources.length} units selected · ${citedIds.size} cited in answer`))
  const list = node('div', 'evidence-list')
  for (const source of sources) {
    const row = button(`evidence-row${active.id === source.id ? ' active' : ''}`, null, () => {
      state.activeSourceId = source.id
      renderEvidence()
    })
    row.append(node('strong', '', source.citation || source.id), node('span', '', `${(source.unit_type || 'provision').replaceAll('_', ' ')}${citedIds.has(source.id) ? ' · cited' : ''}`))
    list.append(row)
  }
  els.evidence.append(list)
  const detail = node('section', 'source-detail')
  detail.append(node('div', 'eyebrow', 'SELECTED UNIT'), node('h3', '', active.citation || active.id), node('div', 'source-id', active.id))
  if (Array.isArray(active.table_columns) && active.table_columns.length) {
    detail.append(node('div', 'table-columns', `Table columns: ${active.table_columns.join(' · ')}`))
  }
  detail.append(node('p', '', active.text || 'This unit provides structural or table-header context.'), node('div', 'source-reason', (active.inclusion_reason || 'selected evidence').replaceAll('_', ' ')))
  els.evidence.append(detail)
}

function render() {
  renderThreads()
  renderMessages()
  renderEvidence()
  refreshComposer()
}

function closeSidebar() { shell.classList.remove('sidebar-open'); updateOverlay() }
function openEvidence() { shell.classList.remove('evidence-hidden'); shell.classList.add('evidence-open'); updateOverlay() }
function closeEvidence() { shell.classList.add('evidence-hidden'); shell.classList.remove('evidence-open'); updateOverlay() }
function updateOverlay() {
  els.overlay.hidden = !(shell.classList.contains('sidebar-open') ||
    (window.innerWidth <= 1200 && shell.classList.contains('evidence-open')))
}

async function sendQuestion() {
  const question = els.question.value.trim()
  if (!question || state.sending || state.health?.status !== 'configured') return
  const threadId = state.activeId
  appendMessage(threadId, { id: crypto.randomUUID(), role: 'user', text: question, time: Date.now() })
  els.question.value = ''
  state.sending = true
  render()
  try {
    const filters = Object.fromEntries(Object.entries(state.filters).filter(([, value]) => value))
    const response = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, filters }),
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The request could not be completed.')
    const message = { id: crypto.randomUUID(), role: 'assistant', result: body, time: Date.now() }
    appendMessage(threadId, message)
    if (threadId === state.activeId) {
      state.activeMessageId = message.id
      state.activeSourceId = body.sources?.[0]?.id || null
    }
  } catch (error) {
    appendMessage(threadId, { id: crypto.randomUUID(), role: 'error', text: error.message, time: Date.now() })
  } finally {
    state.sending = false
    render()
    checkHealth()
    els.question.focus()
  }
}

function loadFilterForm() {
  $('filterChapter').value = state.filters.chapter
  $('filterSection').value = state.filters.section
  $('filterUnitType').value = state.filters.unit_type
  $('filterJurisdiction').value = state.filters.jurisdiction
}

$('newChat').addEventListener('click', newThread)
$('openSidebar').addEventListener('click', () => { shell.classList.add('sidebar-open'); updateOverlay() })
$('closeSidebar').addEventListener('click', closeSidebar)
$('toggleEvidence').addEventListener('click', () => {
  if (shell.classList.contains('evidence-hidden') ||
      (window.innerWidth <= 1200 && !shell.classList.contains('evidence-open'))) openEvidence()
  else closeEvidence()
})
$('closeEvidence').addEventListener('click', closeEvidence)
els.overlay.addEventListener('click', () => { closeSidebar(); closeEvidence() })
$('retryHealth').addEventListener('click', checkHealth)
$('openFilters').addEventListener('click', () => { loadFilterForm(); els.dialog.showModal() })
$('closeFilters').addEventListener('click', () => els.dialog.close())
$('resetFilters').addEventListener('click', () => {
  state.filters = { chapter: '', section: '', unit_type: '', jurisdiction: 'India' }
  loadFilterForm()
})
$('filterForm').addEventListener('submit', (event) => {
  event.preventDefault()
  state.filters = {
    chapter: $('filterChapter').value.trim(), section: $('filterSection').value.trim(),
    unit_type: $('filterUnitType').value, jurisdiction: $('filterJurisdiction').value.trim(),
  }
  els.dialog.close()
  refreshComposer()
})
$('composerForm').addEventListener('submit', (event) => { event.preventDefault(); sendQuestion() })
els.question.addEventListener('input', refreshComposer)
els.question.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendQuestion() }
})
window.addEventListener('resize', updateOverlay)

render()
checkHealth()
