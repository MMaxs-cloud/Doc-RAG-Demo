import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Download, X } from 'lucide-react'
import { api, type Block, type Content, type DocumentItem, type Source } from './api'

export type OpenDocument = { id: string; source?: Source }

function useScroll(key: string | number | undefined) {
  const anchor = useRef<HTMLDivElement>(null)
  useEffect(() => {
    anchor.current?.scrollIntoView({ block: 'center' })
  }, [key])
  return anchor
}

function TextViewer({ text, source }: { text: string; source?: Source }) {
  const first = source?.source_location.start_line
  const last = source?.source_location.end_line ?? first
  const anchor = useScroll(first)
  return <div className="text-view">
    {text.split(/\r\n|\n|\r/).map((line, i) => <div
      ref={first === i + 1 ? anchor : undefined} key={i}
      className={first && last && first <= i + 1 && i + 1 <= last ? 'line marked' : 'line'}>
      <span aria-hidden="true">{i + 1}</span><code>{line || ' '}</code>
    </div>)}
  </div>
}

function MarkdownViewer({ content, source }: { content: Content; source?: Source }) {
  const [lines, setLines] = useState(Boolean(source?.source_location.start_line))
  return <>
    <div className="viewer-controls">
      <button aria-pressed={!lines} onClick={() => setLines(false)}>Структура</button>
      <button aria-pressed={lines} onClick={() => setLines(true)}>Исходные строки</button>
    </div>
    {lines ? <TextViewer text={content.source_text ?? content.canonical_text} source={source} />
      : <StructuredText blocks={content.source_blocks} source={source} />}
  </>
}

function StructuredText({ blocks, source }: { blocks: Block[]; source?: Source }) {
  const highlighted = new Set(source?.source_location.blocks.map(block => block.block_id))
  const first = source?.source_location.blocks[0]?.block_id
  const anchor = useScroll(first)
  return <article className="structured-view">
    {blocks.map(block => <div ref={first === block.block_id ? anchor : undefined} key={block.block_id}
      className={highlighted.has(block.block_id) ? 'marked structural-block' : 'structural-block'}
      data-block={block.block_id}>
      {block.block_type === 'heading' ? <h3>{block.text}</h3>
        : block.block_type === 'code' ? <pre>{block.text}</pre>
        : block.block_type === 'quote' ? <blockquote>{block.text}</blockquote>
        : <p>{block.block_type === 'list_item' && '• '}{block.text}</p>}
    </div>)}
  </article>
}

function HtmlViewer({ content, source }: { content: Content; source?: Source }) {
  return <StructuredText blocks={content.source_blocks} source={source} />
}

type Overlay = { left: number; top: number; width: number; height: number }

function pageOverlays(source: Source | undefined, page: number, width: number, height: number): Overlay[] {
  return (source?.source_location.blocks ?? []).flatMap(block => {
    const coords = block.coordinates
    const box = coords?.bbox
    if (block.page_number !== page || !box) return []
    let x1: number, y1: number, x2: number, y2: number
    if (Array.isArray(box) && box.length === 4 && coords?.origin === 'top_left') {
      ;[x1, y1, x2, y2] = box
    } else if (!Array.isArray(box) && box.coord_origin?.toLowerCase() === 'bottomleft') {
      x1 = box.l; x2 = box.r; y1 = height - box.t; y2 = height - box.b
    } else if (!Array.isArray(box) && box.coord_origin?.toLowerCase() === 'topleft') {
      x1 = box.l; x2 = box.r; y1 = box.t; y2 = box.b
    } else return []
    if (![x1, x2, y1, y2].every(Number.isFinite) || x1 < 0 || x1 >= x2 || x2 > width ||
      Math.min(y1, y2) < 0 || Math.max(y1, y2) > height) return []
    return [{ left: 100*x1/width, top: 100*Math.min(y1,y2)/height,
      width: 100*(x2-x1)/width, height: 100*Math.abs(y2-y1)/height }]
  })
}

function PdfViewer({ url, source, onUnavailable }: {
  url: string; source?: Source; onUnavailable?: () => void
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [page, setPage] = useState(source?.source_location.start_page || 1)
  const [pageCount, setPageCount] = useState(0)
  const [overlays, setOverlays] = useState<Overlay[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)
  const [pdf, setPdf] = useState<import('pdfjs-dist').PDFDocumentProxy | null>(null)

  useEffect(() => {
    let cancelled = false
    let task: import('pdfjs-dist').PDFDocumentLoadingTask | undefined
    void (async () => {
      try {
        const [lib, worker] = await Promise.all([
          import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')
        ])
        if (cancelled) return
        lib.GlobalWorkerOptions.workerSrc = worker.default
        task = lib.getDocument({ url: url.split('#')[0], isEvalSupported: false, enableXfa: false,
          useSystemFonts: true, disableFontFace: true })
        const doc = await task.promise
        if (!cancelled) {
          setPageCount(doc.numPages)
          setPage(value => Math.min(value, doc.numPages))
          setPdf(doc)
        }
      } catch {
        if (!cancelled) {
          setError('Не удалось открыть PDF')
          setBusy(false)
          onUnavailable?.()
        }
      }
    })()
    return () => {
      cancelled = true
      void task?.destroy()
    }
  }, [url, onUnavailable])

  useEffect(() => {
    if (!pdf || !canvas.current) return
    let cancelled = false
    let task: import('pdfjs-dist').RenderTask | undefined
    const canvasElement = canvas.current
    setBusy(true)
    setOverlays([])
    setError("")
    void pdf.getPage(page).then(pdfPage => {
      if (cancelled) return
      const natural = pdfPage.getViewport({ scale: 1 })
      const viewport = pdfPage.getViewport({ scale: Math.min(1.6, 1100 / natural.width, 2400 / natural.height) })
      canvasElement.width = viewport.width
      canvasElement.height = viewport.height
      task = pdfPage.render({ canvas: canvasElement, viewport })
      return task.promise.then(() => {
        if (!cancelled && !pdfPage.rotate) setOverlays(pageOverlays(source, page, natural.width, natural.height))
      })
    }).catch(() => {
      if (!cancelled) setError('Не удалось отобразить страницу PDF')
    }).finally(() => {
      if (!cancelled) setBusy(false)
    })
    return () => {
      cancelled = true
      task?.cancel()
    }
  }, [page, pdf, source])

  return <div className="pdf-view">
    <div className="viewer-controls">
      <button aria-label="Предыдущая страница" disabled={page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft size={16}/></button>
      <span>Страница {page}{pageCount > 0 ? ` из ${pageCount}` : ''}</span>
      <button aria-label="Следующая страница" disabled={page >= pageCount} onClick={() => setPage(page + 1)}><ChevronRight size={16}/></button>
    </div>
    {busy && <p className="muted">Загрузка страницы…</p>}
    {error && <p role="alert">{error}</p>}
    <div className="pdf-paper" hidden={Boolean(error)}>
      <canvas ref={canvas} aria-label={`Страница ${page} документа`}/>
      {overlays.map((mark, i) => <span className="pdf-mark" aria-hidden="true" key={i} style={{
        left: mark.left + "%", top: mark.top + "%", width: mark.width + "%", height: mark.height + "%"
      }}/>) }
    </div>
  </div>
}

export function DocumentViewer({ selection, close }: { selection: OpenDocument; close: () => void }) {
  const [doc, setDoc] = useState<DocumentItem | null>(null)
  const [content, setContent] = useState<Content | null>(null)
  const [error, setError] = useState('')
  const [wordPdf, setWordPdf] = useState(!selection.source)
  const unavailable = useCallback(() => setWordPdf(false), [])
  const dialog = useRef<HTMLDialogElement>(null)
  const source = selection.source

  useEffect(() => { dialog.current?.showModal() }, [])
  useEffect(() => {
    const abort = new AbortController()
    const opts = { signal: abort.signal }
    void api<DocumentItem>(`/documents/${selection.id}`, opts).then(item => {
      setDoc(item)
      if (item.active_version && item.format !== 'pdf') {
        return api<Content>(`/documents/${selection.id}/content`, opts).then(setContent)
      }
    }).catch(e => { if (!abort.signal.aborted) setError(e.message) })
    return () => abort.abort()
  }, [selection.id])

  const url = `/api/v1/documents/${selection.id}`
  let view
  if (error) view = <p role="alert" className="notice error">{error}</p>
  else if (!doc) view = <p className="muted">Загрузка документа…</p>
  else if (doc.format === 'pdf') view = <PdfViewer url={url + '/preview'} source={source} />
  else if (!content) view = <p className="muted">Текст будет доступен после обработки документа.</p>
  else if (doc.format === 'txt') view = <TextViewer text={content.source_text ?? content.canonical_text} source={source} />
  else if (doc.format === 'md') view = <MarkdownViewer content={content} source={source} />
  else if (doc.format === 'html') view = <HtmlViewer content={content} source={source} />
  else view = <>
    <div className="viewer-controls">
      <button aria-pressed={!wordPdf} onClick={() => setWordPdf(false)}>Текст</button>
      {!content.warnings.some(value => value.startsWith('libreoffice_') || value.startsWith('office_conversion_')) &&
        <button aria-pressed={wordPdf} onClick={() => setWordPdf(true)}>PDF-копия</button>}
    </div>
    {wordPdf
      ? <PdfViewer url={url + '/preview'} onUnavailable={unavailable} />
      : <StructuredText blocks={content.source_blocks} source={source} />}
  </>

  return <dialog className="viewer" ref={dialog} onCancel={event => { event.preventDefault(); close() }}>
    <header className="viewer-header">
      <div><span className="eyebrow">Просмотр документа</span><h2>{doc?.title ?? source?.title ?? 'Документ'}</h2></div>
      <div className="inline">
        {doc && <a className="icon-button" href={url + '/file'} aria-label="Скачать оригинал"><Download size={18}/></a>}
        <button className="icon-button" onClick={close} aria-label="Закрыть"><X size={20}/></button>
      </div>
    </header>
    <div className="viewer-body">{view}</div>
    {source && <aside className="viewer-quote"><strong>Найденный фрагмент</strong><p>{source.text}</p></aside>}
  </dialog>
}
