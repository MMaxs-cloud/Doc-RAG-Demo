import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Activity, ArrowUp, BookOpen, ChevronRight, CircleHelp, Copy, Database, Download,
  FileText, FolderOpen, MessageSquare, Moon, Plus, RefreshCw, Settings2, Square, Sun, Trash2 } from 'lucide-react'
import { api, ChatStreamError, formats, json, locationLabel, statusLabels, streamChat, type ChatStage, type Diagnostics, type Page,
  type Preferences, type Provider, type ProviderStatus, type Session, type Settings, type Source, type Turn } from './api'
import { DocumentViewer, type OpenDocument } from './DocumentViewer'
import { DocumentsPage } from './DocumentsPage'

type Route = 'chat' | 'documents' | 'settings' | 'system'
const navigation = [
  { route: 'chat', label: 'Чат', icon: MessageSquare },
  { route: 'documents', label: 'Документы', icon: FolderOpen },
  { route: 'settings', label: 'Настройки', icon: Settings2 },
  { route: 'system', label: 'Диагностика', icon: Activity }
] as const
const defaults: Preferences = { provider: 'local', mode: 'hybrid', top_k: 8, temperature: .1, context_tokens: 5000, max_output_tokens: 1000 }
const providers: Record<Provider, string> = { local: 'GigaChat Lightning', api: 'GigaChat API' }
const warningLabels: Record<string, string> = {
  invalid_citations: 'Модель указала ссылки, которые не удалось подтвердить.',
  no_citations: 'Ответ модели не содержит проверенных ссылок.',
  no_context: 'Для ответа не нашлось доступного контекста.',
  sources_changed: 'Часть исходных фрагментов изменена или удалена.',
  empty_response: 'Модель вернула пустой ответ.',
  generation_timeout: 'Истекло время генерации. Ответ мог оборваться.'
}
const stageLabels: Record<ChatStage, string> = {
  searching: 'Ищу источники…', preparing: 'Подготавливаю запрос…',
  reading: 'Модель читает контекст…', generating: 'Генерирую ответ…'
}

function getRoute(): Route {
  return navigation.find(item => item.route === location.hash.slice(1))?.route ?? 'chat'
}

function SourceCard({ source, group, open }: { source: Source; group: string; open: (doc: OpenDocument) => void }) {
  const location = locationLabel(source.source_location)
  return <article className="source-card" id={`${group}-${source.source_id}`} tabIndex={-1}>
    <div className="source-head"><span className="citation-id">{source.source_id}</span><div>
      <button className="source-title" onClick={() => open({ id: source.document_id, source })}>{source.title}</button>
      <small>{source.original_filename}{location && ' · ' + location}</small></div>
      <span className="format-tag">{source.original_format.toUpperCase()}</span></div>
    <p className="source-excerpt">{(source.context_text ?? source.text).slice(0, 260)}{(source.context_text ?? source.text).length > 260 ? "…" : ""}</p>
    <details><summary>Полный фрагмент</summary><pre>{source.text}</pre></details>
    <div className="inline">
      <button className="text-button" onClick={() => open({ id: source.document_id, source })}><BookOpen size={15}/> Открыть источник</button>
      <a className="text-button" href={source.document_url}><Download size={15}/> Скачать</a>
    </div>
  </article>
}

function Answer({ turn, busy, stage, open, regenerate }: {
  turn: Turn; busy?: boolean; stage?: ChatStage; open: (doc: OpenDocument) => void; regenerate?: () => void
}) {
  const sources = [...turn.cited, ...turn.retrieved.filter(item => !turn.cited.find(cited => cited.source_id === item.source_id))]
  const [copied, setCopied] = useState(false)
  const parts = turn.answer.split(/(\[\s*S\d+(?:\s*,\s*S\d+)*\s*\])/gi)
  return <section className="answer">
    <div className="answer-heading"><span className="assistant-icon"><Database size={16}/></span>
      <strong>{turn.answer || busy ? providers[turn.provider] : 'Результаты поиска'}</strong>
      {turn.warnings.includes('free_chat') && <span className="muted">Без поиска по документам</span>}
      {busy && stage && <span className="muted" role="status">{stageLabels[stage]}</span>}
    </div>
    {turn.answer && <div className="answer-text">{parts.map((part, index) => {
      const ids = part.match(/S\d+/gi)
      if (index % 2 === 0 || !ids || !ids.every(id => sources.some(source => source.source_id === id.toUpperCase()))) return part
      return ids.map(id => <button className="citation-id" key={id} onClick={() => {
        const source = sources.find(item => item.source_id === id.toUpperCase())
        if (source) open({ id: source.document_id, source, answerContext: parts[index - 1]?.trim().split('\n').at(-1) })
      }}>{id.toUpperCase()}</button>)
    })}</div>}
    {turn.warnings.filter(warning => warning !== 'free_chat').map(warning => <p key={warning} className="notice" role="status">{warningLabels[warning] ?? warning}</p>)}
    {sources.length > 0 && <div className="source-results">
      <h3>{turn.cited.length ? 'Источники и найденные фрагменты' : 'Найденные фрагменты'} <span>{sources.length}</span></h3>
      {sources.map(source => <SourceCard key={source.source_id} group={turn.id} source={source} open={open}/>)}
    </div>}
    {!busy && (turn.answer || regenerate) && <div className="inline answer-actions">
      {turn.answer && <button className="text-button" onClick={() => { void navigator.clipboard.writeText(turn.answer).then(() => setCopied(true)) }}>
        <Copy size={15}/>{copied ? 'Скопировано' : 'Копировать'}</button>}
      {regenerate && <button className="text-button" onClick={regenerate}><RefreshCw size={15}/>{turn.warnings.includes('generation_timeout') && !turn.warnings.includes('free_chat') ? 'Повторить с меньшим контекстом' : 'Повторить генерацию'}</button>}
    </div>}
  </section>
}

function ChatPage({ sessionId, setSession, refreshSessions, preferences, open }: {
  sessionId: string | null; setSession: (s: string) => void; refreshSessions: () => void
  preferences: Preferences; open: (doc: OpenDocument) => void
}) {
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [active, setActive] = useState<Turn | null>(null)
  const [provider, setProvider] = useState(preferences.provider)
  const [chatMode, setChatMode] = useState<'grounded' | 'search' | 'free'>('grounded')
  const [stage, setStage] = useState<ChatStage>('searching')
  const [format, setFormat] = useState('')
  const [consent, setConsent] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  const feed = useRef<HTMLDivElement>(null)

  useEffect(() => setProvider(preferences.provider), [preferences.provider])
  useEffect(() => {
    setTurns([]); setActive(null); setError('')
    if (!sessionId) return
    const abort = new AbortController()
    void api<{ messages: Turn[] }>(`/chat/sessions/${sessionId}`, { signal: abort.signal })
      .then(result => setTurns(result.messages)).catch(e => { if (!abort.signal.aborted) setError(e.message) })
    return () => abort.abort()
  }, [sessionId])
  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => {
    if (feed.current && (active || turns.length)) feed.current.scrollTop = feed.current.scrollHeight
  }, [active, turns])

  async function run(value: string, reuse?: Turn) {
    const text = value.trim()
    if (!text || request.current) return
    const freeChat = reuse ? reuse.warnings.includes('free_chat') : chatMode === 'free'
    const onlySearch = chatMode === 'search' && !reuse
    if ((!onlySearch || reuse) && provider === 'api' && !consent) {
      setError('Подтвердите передачу фрагментов в GigaChat API.'); return
    }
    const controller = new AbortController()
    request.current = controller
    setError(''); setQuestion('')
    const id = crypto.randomUUID()
    const draft: Turn = { id, question: text, answer: '', provider, cited: [], retrieved: [], warnings: freeChat ? ['free_chat'] : [] }
    let streamedAnswer = ''
    let streamedSources: Source[] = []
    setStage(freeChat || reuse ? 'preparing' : 'searching')
    setActive(draft)
    let newlyCreatedSession: string | null = null
    try {
      const body = { query: text, mode: preferences.mode, top_k: preferences.top_k, filters: format ? { format } : {} }
      if (onlySearch && !reuse) {
        const result = await api<{ sources: Source[] }>('/search', json('POST', body, controller.signal))
        setTurns(old => [...old, { ...draft, retrieved: result.sources, warnings: result.sources.length ? [] : ['no_context'] }])
      } else {
        let sid = sessionId
        if (!sid) {
          const session = await api<Session>('/chat/sessions', json('POST', {}, controller.signal))
          newlyCreatedSession = sid = session.id
        }
        const result = await streamChat({ ...body, session_id: sid, provider, cloud_consent: consent,
          free_chat: freeChat, reuse_message_id: reuse?.id,
          context_tokens: reuse?.warnings.includes('generation_timeout') && !freeChat ? Math.max(256, Math.floor(preferences.context_tokens / 2)) : undefined }, token => {
          streamedAnswer += token
          setActive(old => old ? { ...old, answer: old.answer + token } : old)
        }, controller.signal, (next, sources) => {
          setStage(next)
          if (sources) {
            streamedSources = sources
            setActive(old => old ? { ...old, retrieved: sources } : old)
          }
        })
        setTurns(old => [...old, { ...draft, id: result.message_id, answer: result.answer, cited: result.sources,
          retrieved: result.retrieved_sources, warnings: result.warnings }])
        if (newlyCreatedSession) setSession(newlyCreatedSession)
        refreshSessions()
      }
    } catch (error) {
      if (error instanceof ChatStreamError && error.messageId) {
        setTurns(old => [...old, { ...draft, id: error.messageId!, answer: streamedAnswer,
          retrieved: streamedSources, warnings: [...draft.warnings, 'generation_timeout'] }])
        if (newlyCreatedSession) setSession(newlyCreatedSession)
        refreshSessions()
      } else {
        setError(controller.signal.aborted ? 'Запрос отменён.' : (error as Error).message)
        if (!reuse) setQuestion(text)
      }
    } finally {
      request.current = null; setActive(null)
    }
  }

  function onSubmit(event: FormEvent) { event.preventDefault(); void run(question) }
  return <div className="chat-page">
    <header className="chat-header"><div><h1>Чат с документами</h1><p>Поиск и ответы по техническому корпусу</p></div>
      <label className="provider-select"><span className="service-dot"/><select aria-label="Модель генерации"
        value={provider} onChange={e => { setProvider(e.target.value as Provider); setConsent(false) }}>
        <option value="local">GigaChat Lightning · Локально</option><option value="api">GigaChat · API</option>
      </select></label>
    </header>
    <div className="chat-feed" ref={feed}>
      {!turns.length && !active && <div className="welcome">
        <div className="welcome-icon"><BookOpen size={28} strokeWidth={1.7}/></div>
        <h2>{chatMode === 'free' ? 'О чём поговорим?' : 'Что найти в документах?'}</h2>
        {chatMode === 'free' ? <p>Модель использует историю этого чата.</p> : <>
          <p>Задайте вопрос по вашему корпусу. В ответе будут исходные фрагменты и ссылки на документы.</p>
          <a className="text-button" href="#documents"><FolderOpen size={16}/> Открыть документы <ChevronRight size={15}/></a>
        </>}
      </div>}
      {turns.map(turn => <div className="turn" key={turn.id}>
        <div className="question">{turn.question}</div>
        <Answer turn={turn} open={open} regenerate={(turn.answer || turn.warnings.includes('generation_timeout')) && !active && sessionId ? () => void run(turn.question, turn) : undefined}/>
      </div>)}
      {active && <div className="turn"><div className="question">{active.question}</div>
        <Answer turn={active} open={open} busy stage={stage}/>
      </div>}
    </div>
    <footer className="composer-area">
      {error && <p className="notice error" role="alert">{error}</p>}
      {provider === 'api' && <label className="cloud-consent">
        <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)}/> Разрешаю отправить вопрос, историю и, при поиске, найденные фрагменты в GigaChat API
      </label>}
      <form className="composer" onSubmit={onSubmit}>
        <textarea aria-label="Вопрос" placeholder={chatMode === 'free' ? 'Напишите сообщение…' : 'Спросите о содержании документов…'} rows={2}
          value={question} onChange={e => setQuestion(e.target.value)} maxLength={4000} onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void run(question) }
          }}/>
        <div className="composer-bar"><div className="inline">
          <select aria-label="Режим чата" value={chatMode} disabled={!!active} onChange={e => setChatMode(e.target.value as typeof chatMode)}>
            <option value="grounded">Ответ с источниками</option><option value="search">Только поиск</option>
            <option value="free">Свободный чат</option>
          </select>
          {chatMode !== 'free' && <select aria-label="Формат источников" value={format} onChange={e => setFormat(e.target.value)}>
            <option value="">Все форматы</option>{formats.map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}
          </select>}
        </div>
        {active ? <button type="button" className="send" aria-label="Отменить запрос" onClick={() => request.current?.abort()}><Square size={16}/></button>
          : <button type="submit" className="send" aria-label="Отправить вопрос" disabled={!question.trim()}><ArrowUp size={19}/></button>}</div>
      </form>
    </footer>
  </div>
}

function SettingsPage({ settings, saved }: { settings: Settings | null; saved: (prefs: Settings) => void }) {
  const [values, setValues] = useState<Preferences>(settings ?? defaults)
  const [status, setStatus] = useState<ProviderStatus | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (settings) setValues(settings) }, [settings])
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true)
    try {
      const { provider, mode, top_k, temperature, context_tokens, max_output_tokens } = values
      await api('/settings', json('PATCH', { provider, mode, top_k, temperature, context_tokens, max_output_tokens }))
      saved(await api<Settings>('/settings')); setNotice('Настройки сохранены')
    } catch (error) { setNotice((error as Error).message) } finally { setBusy(false) }
  }
  return <div className="page narrow">
    <header className="page-heading"><div><h1>Настройки</h1><p>Модели, поиск и контекст ответа</p></div></header>
    <form className="panel settings-form" onSubmit={save}>
      <h2>Параметры запросов</h2><div className="field-grid">
        <label>Модель<select value={values.provider} onChange={e => setValues({ ...values, provider: e.target.value as Provider })}>
          <option value="local">GigaChat Lightning · Локально</option><option value="api">GigaChat API</option></select></label>
        <label>Режим поиска<select value={values.mode} onChange={e => setValues({ ...values, mode: e.target.value as Preferences['mode'] })}>
          <option value="hybrid">Гибридный</option><option value="dense">Dense</option><option value="bm25">BM25</option></select></label>
        {([
          ['top_k', 'Количество фрагментов', 1, 30, 1], ['temperature', 'Temperature', 0, 2, .05],
          ['context_tokens', 'Бюджет контекста', 256, 16000, 1], ['max_output_tokens', 'Максимум токенов ответа', 32, 4096, 1]
        ] as const).map(([field, label, min, max, step]) => <label key={field}>{label}
          <input type="number" min={min} max={max} step={step} required value={values[field]}
            onChange={e => setValues({ ...values, [field]: Number(e.target.value) })}/></label>)}
      </div><div className="inline"><button className="primary" disabled={busy}>Сохранить</button><span role="status">{notice}</span></div>
    </form>
    <section className="panel"><h2>Индекс и embeddings</h2>
      <p className="muted">Изменение этих параметров применяется при перестроении индекса.</p>
      {settings && <dl><dt>Модель</dt><dd>{settings.embeddings.model}</dd><dt>Размерность</dt><dd>{settings.embeddings.dimension}</dd>
        <dt>Размер / перекрытие</dt><dd>{settings.chunking.size} / {settings.chunking.overlap}</dd><dt>Версия индекса</dt><dd>{settings.index_version}</dd></dl>}</section>
    <section className="panel"><h2>Провайдеры</h2>
      <button onClick={() => void api<ProviderStatus>('/providers').then(setStatus).catch(error => setNotice(error.message))}><RefreshCw size={15}/> Проверить подключение</button>
      {status && <ul className="status-list">{(['local', 'api'] as const).map(key => <li key={key}>
        {providers[key]} <span className={status[key].available ? 'status-pill ready' : 'status-pill failed'}>{status[key].available ? 'Доступен' : 'Недоступен'}</span>
        {key === 'api' && <small>{status.api.cloud_allowed ? 'Передача разрешена настройками сервера' : 'Передача запрещена настройками сервера'}</small>}
      </li>)}</ul>}
    </section>
  </div>
}

function SystemPage() {
  const [status, setStatus] = useState<Diagnostics | null>(null)
  const [error, setError] = useState('')
  const refresh = () => { setError(''); void api<Diagnostics>('/system/status').then(setStatus).catch(e => setError(e.message)) }
  useEffect(refresh, [])
  return <div className="page narrow">
    <header className="page-heading"><div><h1>Диагностика</h1><p>Состояние сервисов и ресурсы</p></div>
      <button onClick={refresh}><RefreshCw size={16}/> Обновить</button></header>
    {error && <p className="notice error" role="alert">{error}</p>}
    {!status && !error && <p className="muted">Проверка сервисов…</p>}
    {status && <>
      <div className="metrics"><div className="panel"><span>Документы</span><strong>{status.counts.documents}</strong></div>
        <div className="panel"><span>Фрагменты</span><strong>{status.counts.chunks}</strong></div>
        <div className="panel"><span>Индекс</span><strong className="hash">{status.index_version}</strong></div></div>
      <section className="panel"><h2>Сервисы</h2><ul className="status-list">
        {Object.entries({ Backend: status.backend, Qdrant: status.qdrant.available, Worker: status.worker.available,
          Embeddings: status.embeddings.available, 'GigaChat Lightning': status.providers.local.available,
          'GigaChat API': status.providers.api.available, LibreOffice: ['available', 'isolated'].includes(status.libreoffice ?? '') }).map(([name, up]) =>
          <li key={name}>{name} <span className={up ? 'status-pill ready' : 'status-pill failed'}>{up ? 'Доступен' : 'Недоступен'}</span></li>)}
      </ul></section>
      <section className="panel"><h2>Ресурсы</h2><dl><dt>RAM системы</dt>
        <dd>{status.hardware.system_used_ram_mib} / {status.hardware.system_ram_mib} MiB</dd>
        <dt>RAM backend</dt><dd>{status.hardware.backend_rss_mib} MiB</dd>
        {status.hardware.gpu.map(gpu => <div className="resource" key={gpu.name}><dt>{gpu.name}</dt><dd>{gpu.used_mib} / {gpu.total_mib} MiB VRAM</dd></div>)}
      </dl></section>
      <section className="panel"><h2>Задания</h2><ul className="status-list">{status.jobs.map(job => <li key={job.status}>{statusLabels[job.status]} <strong>{job.count}</strong></li>)}</ul>
        {status.recent_errors.map(issue => <p className="notice error" key={issue.id}>{issue.error_message}</p>)}
        {!status.jobs.length && <p className="muted">Заданий пока нет.</p>}</section>
    </>}
  </div>
}

export function App() {
  const [route, setRoute] = useState(getRoute)
  const [sessions, setSessions] = useState<Session[]>([])
  const [sessionId, setSession] = useState<string | null>(null)
  const [newChat, setNewChat] = useState(0)
  const [selection, setSelection] = useState<OpenDocument | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [dark, setDark] = useState(() => localStorage.getItem('trag-theme') === 'dark')
  const [connected, setConnected] = useState<boolean | null>(null)
  const [error, setError] = useState('')
  const [deleteChat, setDeleteChat] = useState<Session | null>(null)

  function refreshSessions() {
    void api<Page<Session>>('/chat/sessions').then(result => setSessions(result.items)).catch(error => setError(error.message))
  }
  useEffect(() => {
    const change = () => setRoute(getRoute())
    window.addEventListener('hashchange', change)
    void api('/health').then(() => setConnected(true)).catch(() => setConnected(false))
    void api<Settings>('/settings').then(setSettings).catch(() => {})
    refreshSessions()
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    localStorage.setItem('trag-theme', dark ? 'dark' : 'light')
  }, [dark])
  async function removeSession() {
    if (!deleteChat) return
    try {
      await api(`/chat/sessions/${deleteChat.id}`, { method: 'DELETE' })
      if (sessionId === deleteChat.id) setSession(null)
      refreshSessions()
    } catch (error) { setError((error as Error).message) }
    finally { setDeleteChat(null) }
  }
  return <div className="shell">
    <aside className="sidebar">
      <a className="brand" href="#chat"><span className="brand-icon"><Database size={18}/></span><span>Technical RAG<small>Версия 0.6</small></span></a>
      <button className="new-chat" onClick={() => { setSession(null); setNewChat(value => value + 1); location.hash = 'chat' }}><Plus size={18}/> Новый диалог</button>
      <nav aria-label="Основная навигация">{navigation.map(({ route: key, label, icon: Icon }) =>
        <a href={'#' + key} key={key} aria-current={route === key ? 'page' : undefined}><Icon size={18}/>{label}</a>)}</nav>
      <div className="session-list"><h2>История диалогов</h2>
        {sessions.length ? sessions.map(item => <div className={sessionId === item.id ? 'session selected' : 'session'} key={item.id}>
          <button className="session-title" title={item.title} onClick={() => { setSession(item.id); location.hash = 'chat' }}>
            <MessageSquare size={15}/><span>{item.title}</span></button>
          <button className="icon-button" aria-label="Удалить диалог" onClick={() => setDeleteChat(item)}><Trash2 size={14}/></button>
        </div>) : <p>Диалогов пока нет</p>}
      </div>
      <div className="sidebar-footer">
        <div className="connection"><span className={connected ? 'service-dot' : 'service-dot offline'}/>{connected === null ? 'Подключение…' : connected ? 'Backend доступен' : 'Backend недоступен'}</div>
        <button className="icon-button" aria-label={dark ? 'Светлая тема' : 'Тёмная тема'} onClick={() => setDark(!dark)}>
          {dark ? <Sun size={17}/> : <Moon size={17}/>}</button>
      </div>
    </aside>
    <main className="workspace">
      {error && <p className="notice error global-error" role="alert"><CircleHelp size={16}/>{error}</p>}
      {route === 'chat' && <ChatPage key={newChat} sessionId={sessionId} setSession={setSession} refreshSessions={refreshSessions}
        preferences={settings ?? defaults} open={setSelection}/>}
      {route === 'documents' && <DocumentsPage open={setSelection}/>}
      {route === 'settings' && <SettingsPage settings={settings} saved={setSettings}/>}
      {route === 'system' && <SystemPage/>}
    </main>
    {selection && <DocumentViewer key={selection.id} selection={selection} close={() => setSelection(null)}/>}
    {deleteChat && <div className="dialog-backdrop"><div role="alertdialog" aria-label="Удаление диалога" aria-modal="true" className="confirm">
      <h2>Удалить диалог?</h2><p>{deleteChat.title}</p>
      <div className="inline"><button onClick={() => setDeleteChat(null)}>Отмена</button>
        <button className="danger-fill" onClick={() => void removeSession()}>Удалить</button></div>
    </div></div>}
  </div>
}
