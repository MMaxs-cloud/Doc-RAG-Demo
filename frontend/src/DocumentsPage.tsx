import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, FileText, FolderOpen, RefreshCw, Search, Square, Trash2, UploadCloud } from 'lucide-react'
import { api, formats, json, statusLabels, type DocumentItem, type Page } from './api'
import type { OpenDocument } from './DocumentViewer'

export function DocumentsPage({ open }: { open: (doc: OpenDocument) => void }) {
  const [list, setList] = useState<Page<DocumentItem>>({ items: [], total: 0, offset: 0, limit: 15 })
  const [query, setQuery] = useState('')
  const [format, setFormat] = useState('')
  const [status, setStatus] = useState('')
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)
  const [remove, setRemove] = useState<DocumentItem | null>(null)
  const [encoding, setEncoding] = useState<DocumentItem | null>(null)
  const [encodingName, setEncodingName] = useState('windows-1251')
  const fileInput = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: '15', offset: String(offset) })
    if (query.trim()) params.set('query', query.trim())
    if (format) params.set('format', format)
    if (status) params.set('status', status)
    try {
      setList(await api<Page<DocumentItem>>('/documents?' + params, { signal }))
    } catch (error) {
      if (!signal?.aborted) setErrors([String(error)])
    }
  }, [query, format, status, offset])

  useEffect(() => {
    const abort = new AbortController()
    setLoading(true)
    const update = () => { void refresh(abort.signal).finally(() => { if (!abort.signal.aborted) setLoading(false) }) }
    const initial = window.setTimeout(update, 160)
    const poll = window.setInterval(update, 1800)
    return () => { window.clearTimeout(initial); window.clearInterval(poll); abort.abort() }
  }, [refresh])

  async function upload(files: FileList | File[]) {
    if (!files.length || uploading) return
    setUploading(true); setErrors([])
    const batches: File[][] = []
    let bytes = 0
    for (const file of Array.from(files)) {
      if (!batches.length || batches.at(-1)!.length >= 25 || bytes + file.size > 380 * 1024 ** 2) {
        batches.push([]); bytes = 0
      }
      batches.at(-1)!.push(file); bytes += file.size
    }
    const issues: string[] = []
    for (const batch of batches) {
      const body = new FormData()
      batch.forEach(file => body.append('files', file))
      try {
        const result = await api<{ items: { filename?: string; error?: { message: string }; document?: DocumentItem & { duplicate?: boolean } }[] }>(
          '/documents/upload', { method: 'POST', body })
        result.items.forEach(item => {
          if (item.error) issues.push(`${item.filename}: ${item.error.message}`)
          if (item.document?.duplicate) issues.push(`${item.document.filename}: документ уже в корпусе`)
        })
      } catch (error) { issues.push(String(error)) }
    }
    setErrors(issues); setUploading(false); void refresh()
    if (fileInput.current) fileInput.current.value = ''
  }

  async function action(path: string, method: string, values: object = {}) {
    try {
      await api(path, json(method, values)); setErrors([]); await refresh()
    } catch (error) { setErrors([String(error)]) }
  }

  return <div className="page">
    <header className="page-heading">
      <div><h1>Документы</h1><p>Загрузка и индексация технического корпуса</p></div>
      <button className="primary" onClick={() => fileInput.current?.click()} disabled={uploading}>
        <UploadCloud size={18}/> {uploading ? 'Загрузка…' : 'Загрузить файлы'}
      </button>
      <input ref={fileInput} className="visually-hidden" type="file" multiple aria-label="Выберите документы"
        accept=".pdf,.doc,.docx,.txt,.md,.html,.htm" onChange={e => { if (e.target.files) void upload(e.target.files) }} />
    </header>
    <div className={dragging ? 'drop-zone dragging' : 'drop-zone'} onDragOver={event => {
      event.preventDefault(); setDragging(true)
    }} onDragLeave={() => setDragging(false)} onDrop={event => {
      event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files)
    }}>
      <div className="drop-icon"><UploadCloud size={23}/></div>
      <strong>Перетащите документы сюда</strong>
      <p>PDF, DOC, DOCX, TXT, Markdown и HTML</p>
    </div>
    {errors.length > 0 && <div className="notice error" role="alert">{errors.map((error, i) => <p key={i}>{error}</p>)}</div>}
    <div className="filters">
      <label className="searchbox"><Search size={17}/><input aria-label="Поиск по названию" placeholder="Поиск по названию"
        value={query} onChange={e => { setQuery(e.target.value); setOffset(0) }}/></label>
      <select aria-label="Форматы" value={format} onChange={e => { setFormat(e.target.value); setOffset(0) }}>
        <option value="">Все форматы</option>{formats.map(item => <option key={item} value={item}>{item.toUpperCase()}</option>)}
      </select>
      <select aria-label="Статусы" value={status} onChange={e => { setStatus(e.target.value); setOffset(0) }}>
        <option value="">Все статусы</option>{Object.entries(statusLabels).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
      </select>
    </div>
    <div className="table-panel">
    <table><thead><tr><th>Документ</th><th>Формат</th><th>Статус</th><th>Фрагменты</th><th>Дата</th><th>Действия</th></tr></thead>
      <tbody>{list.items.map(doc => <tr key={doc.id}>
        <td><button className="document-name" onClick={() => open({ id: doc.id })}><FileText size={17}/>{doc.title}</button>
          <span className="file-subtitle">{doc.filename} · {Math.max(1, Math.round(doc.size / 1024))} КБ
            {doc.metadata.page_count ? ` · ${doc.metadata.page_count} стр.` : ''}</span>
          {doc.job?.error_message && <span className="file-error">{doc.job.error_message}</span>}</td>
        <td><span className="format-tag">{doc.format.toUpperCase()}</span></td>
        <td><span className={`status-pill ${doc.status}`}>{statusLabels[doc.status]}</span>
          {doc.status === 'processing' && <progress max={100} value={doc.job?.progress ?? 0} aria-label="Прогресс индексации"/>}</td>
        <td>{doc.chunk_count ?? '—'}</td>
        <td>{new Date(doc.created_at * 1000).toLocaleDateString('ru-RU')}</td>
        <td><div className="inline actions">
          <button className="icon-button" aria-label="Открыть" title="Открыть" onClick={() => open({ id: doc.id })}><FolderOpen size={17}/></button>
          <a className="icon-button" aria-label="Скачать оригинал" title="Скачать оригинал" href={`/api/v1/documents/${doc.id}/file`}><Download size={17}/></a>
          {doc.status === 'processing' || doc.status === 'pending'
            ? <button className="icon-button" aria-label="Отменить обработку" title="Отменить обработку" onClick={() =>
              void action(`/jobs/${doc.job!.id}/cancel`, 'POST')}><Square size={15}/></button>
            : <button className="icon-button" aria-label="Переиндексировать" title="Переиндексировать" onClick={() => {
              if (['encoding_ambiguous', 'invalid_encoding'].includes(doc.job?.error_code ?? '')) { setEncoding(doc); return }
              void action(`/documents/${doc.id}/reindex`, 'POST')
            }}><RefreshCw size={16}/></button>}
          <button className="icon-button danger" aria-label="Удалить документ" title="Удалить документ" onClick={() => setRemove(doc)}><Trash2 size={16}/></button>
        </div></td>
      </tr>)}</tbody>
    </table>
    {!list.items.length && <p className="empty-table">{loading ? 'Загрузка…' : 'Документы не найдены'}</p>}
    </div>
    <div className="pagination">
      <span>Документов: {list.total}</span>
      <button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - list.limit))}>Назад</button>
      <button disabled={offset + list.limit >= list.total} onClick={() => setOffset(offset + list.limit)}>Далее</button>
    </div>
    {remove && <div className="dialog-backdrop"><div className="confirm" role="alertdialog" aria-label="Удаление документа" aria-modal="true">
      <h2>Удалить документ?</h2><p>{remove.title} исчезнет из поиска. Оригинал и производные файлы будут удалены.</p>
      <div className="inline"><button onClick={() => setRemove(null)}>Отмена</button><button className="danger-fill" onClick={() => {
        void action(`/documents/${remove.id}`, 'DELETE'); setRemove(null)
      }}>Удалить</button></div>
    </div></div>}
    {encoding && <div className="dialog-backdrop"><div className="confirm" role="dialog" aria-label="Выбор кодировки" aria-modal="true">
      <h2>Кодировка документа</h2><p>{encoding.filename}</p>
      <select aria-label="Кодировка" value={encodingName} onChange={e => setEncodingName(e.target.value)}>
        {['utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'windows-1251', 'windows-1252', 'koi8-r'].map(item => <option key={item}>{item}</option>)}
      </select>
      <div className="inline"><button onClick={() => setEncoding(null)}>Отмена</button><button className="primary" onClick={() => {
        void action(`/documents/${encoding.id}/reindex`, 'POST', { encoding: encodingName }); setEncoding(null)
      }}>Повторить обработку</button></div>
    </div></div>}
  </div>
}
