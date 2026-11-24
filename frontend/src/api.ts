export type Format = 'pdf' | 'doc' | 'docx' | 'txt' | 'md' | 'html'
export type Mode = 'hybrid' | 'dense' | 'bm25'
export type Provider = 'local' | 'api'
export type Status = 'pending' | 'processing' | 'ready' | 'failed' | 'requires_ocr' | 'cancelled'
export type Job = { id: string; status: Status; stage: string; progress: number; error_code?: string; error_message?: string }
export type DocumentItem = {
  id: string; title: string; filename: string; format: Format; mime: string; size: number
  status: Status; created_at: number; active_version: string | null; chunk_count: number | null
  metadata: { page_count?: number; authors?: string[]; language?: string }
  job: Job | null
}
export type Block = {
  block_id: string; block_type?: string; text?: string; page_number?: number
  section_path?: string[]; paragraph_index?: number; start_line?: number; end_line?: number
  start_offset: number; end_offset: number
  coordinates?: { bbox?: number[] | { l: number; t: number; r: number; b: number; coord_origin?: string }; origin?: string }
}
export type Location = {
  start_page?: number; end_page?: number; start_line?: number; end_line?: number
  section_path?: string[]; paragraph_index?: number; end_paragraph_index?: number
  html_element_id?: string; blocks: Block[]
}
export type Source = {
  source_id: string; document_id: string; chunk_id: string; title: string
  original_filename: string; original_format: Format; text: string; context_text?: string
  document_url: string; preview_url: string | null; source_location: Location
}
export type Content = {
  canonical_text: string; source_text: string | null; source_blocks: Block[]
  encoding: string | null; warnings: string[]
}
export type Session = { id: string; title: string; updated_at: number }
export type Turn = {
  id: string; question: string; answer: string; provider: Provider
  cited: Source[]; retrieved: Source[]; warnings: string[]
}
export type Final = {
  answer: string; provider: Provider; sources: Source[]; retrieved_sources: Source[]
  warnings: string[]; message_id: string
}
export type Page<T> = { items: T[]; total: number; offset: number; limit: number }
export type Preferences = {
  provider: Provider; mode: Mode; top_k: number; temperature: number
  context_tokens: number; max_output_tokens: number
}
export type Settings = Preferences & {
  index_version: string; embeddings: { model: string; dimension: number; revision: string }
  chunking: { size: number; overlap: number }
}
export type ProviderStatus = Record<Provider, { available: boolean; model: string; cloud_allowed?: boolean; configured?: boolean }>
export type Diagnostics = {
  backend: boolean; qdrant: { available: boolean }; embeddings: { available: boolean }
  worker: { available: boolean }; libreoffice: string | null
  counts: { documents: number; chunks: number }
  hardware: { system_ram_mib: number; system_used_ram_mib: number; backend_rss_mib: number
    gpu: { name: string; used_mib: number; total_mib: number }[] }
  providers: ProviderStatus; jobs: { status: Status; count: number }[]
  recent_errors: { id: string; error_code: string; error_message: string }[]
  index_version: string
}

export const formats: Format[] = ['pdf', 'doc', 'docx', 'txt', 'md', 'html']
export const statusLabels: Record<Status, string> = {
  pending: 'В очереди', processing: 'Обработка', ready: 'Готов', failed: 'Ошибка',
  requires_ocr: 'Нужен OCR', cancelled: 'Отменено'
}

async function errorMessage(response: Response) {
  const data = await response.json().catch(() => null)
  return data?.error?.message ?? `Ошибка HTTP ${response.status}`
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, options)
  if (!response.ok) throw new Error(await errorMessage(response))
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}

export function json(method: string, value: object, signal?: AbortSignal): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value), signal }
}

export function locationLabel(location: Location) {
  const parts: string[] = []
  const range = (a?: number, b?: number) => a && b && a !== b ? `${a}–${b}` : a
  if (location.start_page) parts.push(`Стр. ${range(location.start_page, location.end_page)}`)
  if (location.section_path?.length) parts.push(location.section_path.join(' › '))
  if (location.paragraph_index) parts.push(`Абз. ${range(location.paragraph_index, location.end_paragraph_index)}`)
  if (location.start_line) parts.push(`Строки ${range(location.start_line, location.end_line)}`)
  if (location.html_element_id) parts.push('#' + location.html_element_id)
  return parts.join(' · ')
}

export function parseEvent(frame: string) {
  const lines = frame.split(/\r?\n/)
  const kind = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
  const body = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
  return kind && body ? { kind, data: JSON.parse(body) } : null
}

export async function streamChat(body: object, onToken: (text: string) => void, signal: AbortSignal) {
  const response = await fetch('/api/v1/chat/stream', json('POST', body, signal))
  if (!response.ok) throw new Error(await errorMessage(response))
  if (!response.body) throw new Error('Пустой поток ответа')
  let buffer = ''
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const event = parseEvent(frame)
        if (event?.kind === 'token') onToken(event.data.text)
        if (event?.kind === 'error') throw new Error(event.data.message)
        if (event?.kind === 'final') return event.data as Final
      }
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
  throw new Error('Поток завершился до проверки источников')
}
