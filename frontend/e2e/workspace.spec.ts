import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test('upload, monitor, search, stream, open, download, switch and delete', async ({ page }) => {
  // Fixture responses cover the browser contract. Real inference is exercised by backend/tests/test_live.py.
  const original = await readFile('../tests/fixtures/thermal-protocol.txt')
  let uploaded = false
  let polls = 0
  let asked = false
  const source = {
    source_id: 'S1', document_id: 'demo', chunk_id: 'chunk', title: 'Протокол',
    original_filename: 'thermal-protocol.txt', original_format: 'txt', text: 'Датчик TS-04',
    document_url: '/api/v1/documents/demo/file', preview_url: null,
    source_location: { start_line: 4, end_line: 4, blocks: [{ block_id: 'b1' }] }
  }
  const answer = 'Стабилизацию проверяют датчиком TS-04 [S1].'
  const settings = {
    mode: 'hybrid', top_k: 8, provider: 'local', temperature: .1, context_tokens: 5000,
    max_output_tokens: 1000, index_version: 'test', embeddings: { model: 'Giga', dimension: 1024 },
    chunking: { size: 512, overlap: 80 }
  }
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    const method = route.request().method()
    const row = {
      id: 'demo', title: 'Протокол', filename: 'thermal-protocol.txt', format: 'txt',
      size: original.length, created_at: 1795495000, metadata: {},
      status: polls > 1 ? 'ready' : 'processing', chunk_count: polls > 1 ? 1 : null,
      active_version: polls > 1 ? 'v1' : null, job: { id: 'job', progress: 55 }
    }
    if (path === '/health') return route.fulfill({ json: { status: 'ok' } })
    if (path === '/settings') return route.fulfill({ json: settings })
    if (path === '/chat/sessions' && method === 'POST') return route.fulfill({ json: { id: 'session' }, status: 201 })
    if (path === '/chat/sessions') return route.fulfill({ json: { items: asked ? [{ id: 'session', title: 'Датчик' }] : [] } })
    if (path === '/chat/sessions/session') return route.fulfill({ json: {
      messages: asked ? [{ id: 'message', question: 'Какой датчик используется?', answer, provider: 'local',
        cited: [source], retrieved: [source], warnings: [] }] : []
    } })
    if (path === '/documents/upload') {
      uploaded = true
      return route.fulfill({ json: { items: [{ document: row }] }, status: 202 })
    }
    if (path === '/documents') {
      if (uploaded) polls++
      return route.fulfill({ json: { items: uploaded ? [row] : [], total: Number(uploaded), limit: 15, offset: 0 } })
    }
    if (path === '/documents/demo' && method === 'DELETE') {
      uploaded = false
      return route.fulfill({ json: { job_id: 'delete' }, status: 202 })
    }
    if (path === '/documents/demo') return route.fulfill({ json: row })
    if (path === '/documents/demo/file') return route.fulfill({ body: original, headers: {
      'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="thermal-protocol.txt"'
    } })
    if (path === '/documents/demo/content') return route.fulfill({ json: { source_text: original.toString(), source_blocks: [], warnings: [] } })
    if (path === '/search') return route.fulfill({ json: { sources: [source] } })
    if (path === '/chat/stream') {
      asked = true
      const options = route.request().postDataJSON()
      if (options.provider === 'api') expect(options.cloud_consent).toBe(true)
      const result = { answer, provider: options.provider, sources: [source], retrieved_sources: [source],
        warnings: [], message_id: options.provider === 'api' ? 'second-message' : 'message' }
      return route.fulfill({ contentType: 'text/event-stream',
        body: 'event: token\ndata: ' + JSON.stringify({ text: answer }) + '\n\nevent: final\ndata: ' + JSON.stringify(result) + '\n\n' })
    }
    return route.fulfill({ status: 404 })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Чат с документами' })).toBeVisible()
  await page.getByRole('link', { name: 'Документы', exact: true }).click()
  await page.locator('input[type=file]').setInputFiles('../tests/fixtures/thermal-protocol.txt')
  await expect(page.getByRole('progressbar')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Готов', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Чат', exact: true }).click()
  await page.getByRole('combobox', { name: 'Режим чата' }).selectOption('search')
  await page.getByRole('textbox', { name: 'Вопрос' }).fill('TS-04')
  await page.getByRole('button', { name: 'Отправить вопрос' }).click()
  await expect(page.getByRole('heading', { name: /Найденные фрагменты/ })).toBeVisible()
  await page.getByRole('combobox', { name: 'Режим чата' }).selectOption('grounded')
  await page.getByRole('textbox', { name: 'Вопрос' }).fill('Какой датчик используется?')
  await page.getByRole('button', { name: 'Отправить вопрос' }).click()
  await expect(page.getByText(/Стабилизацию проверяют/)).toBeVisible()
  await page.getByRole('button', { name: 'S1', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog').locator('.marked')).toContainText('TS-04')
  const download = page.waitForEvent('download')
  await page.getByRole('dialog').getByRole('link', { name: 'Скачать оригинал' }).click()
  expect((await download).suggestedFilename()).toBe('thermal-protocol.txt')
  await page.getByRole('dialog').getByRole('button', { name: 'Закрыть' }).click()
  await page.getByRole('combobox', { name: 'Модель генерации' }).selectOption('api')
  await page.getByRole('checkbox', { name: /Разрешаю отправить/ }).check()
  await page.getByRole('button', { name: 'Повторить генерацию' }).click()
  await expect(page.getByText('GigaChat API', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Документы', exact: true }).click()
  await page.getByRole('button', { name: 'Удалить документ' }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: 'Удалить', exact: true }).click()
  await expect(page.getByText('Документы не найдены')).toBeVisible()
})

test('PDF.js renders the original test document and changes pages', async ({ page }) => {
  const pdf = await readFile('../tests/fixtures/mounting.pdf')
  const table = { block_id: 'table', block_type: 'table', page_number: 2,
    text: '| Test | Requirement | Unit |\n| --- | --- | --- |\n| Load | 28 | W |\n| Resistance | 0.35 | K/W |',
    coordinates: { bbox: [55, 184, 400, 275], origin: 'top_left' } }
  const source = { source_id: 'S1', document_id: 'pdf', document_version: 'v1', chunk_id: 'chunk',
    original_format: 'pdf', original_filename: 'mounting.pdf', title: 'Mounting and heat load',
    text: table.text, source_location: { start_page: 2, blocks: [table] } }
  const doc = { id: 'pdf', title: 'Mounting and heat load', filename: 'mounting.pdf', format: 'pdf',
    size: pdf.length, created_at: 1795495000, metadata: { page_count: 2 },
    status: 'ready', chunk_count: 1, active_version: 'v1', job: null }
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/documents')) return route.fulfill({ json: { items: [doc], total: 1, offset: 0, limit: 15 } })
    if (path.endsWith('/documents/pdf')) return route.fulfill({ json: doc })
    if (path.endsWith('/documents/pdf/preview')) return route.fulfill({ body: pdf, contentType: 'application/pdf' })
    if (path.endsWith('/documents/pdf/content')) return route.fulfill({ json: { source_blocks: [table], warnings: [] } })
    if (path.endsWith('/chat/sessions')) return route.fulfill({ json: { items: [{ id: 'table-chat', title: 'Таблица' }] } })
    if (path.endsWith('/chat/sessions/table-chat')) return route.fulfill({ json: { messages: [{
      id: 'table-answer', provider: 'local', question: 'Какая мощность?',
      answer: 'Мощность составляет 28 Вт [S1].', cited: [source], retrieved: [source], warnings: []
    }] } })
    if (path.endsWith('/health')) return route.fulfill({ json: { status: 'ok' } })
    return route.fulfill({ status: 404 })
  })
  await page.goto('/#documents')
  await page.getByRole('button', { name: 'Mounting and heat load' }).click()
  const viewer = page.getByRole('dialog')
  await expect(viewer.getByText('Страница 1 из 2')).toBeVisible()
  await expect(viewer.getByText('Загрузка страницы…')).toBeHidden()
  expect(await viewer.locator('canvas').evaluate(canvas => (canvas as HTMLCanvasElement).width)).toBeGreaterThan(300)
  await viewer.getByRole('button', { name: 'Следующая страница' }).click()
  await expect(viewer.getByText('Страница 2 из 2')).toBeVisible()
  await expect(viewer.getByText('Загрузка страницы…')).toBeHidden()
  await expect(viewer.getByRole('alert')).toHaveCount(0)
  await viewer.getByRole('button', { name: 'Закрыть' }).click()
  await page.getByRole('button', { name: 'Таблица', exact: true }).click()
  await page.getByRole('button', { name: 'S1', exact: true }).click()
  await expect(viewer.getByText('Страница 2 из 2')).toBeVisible()
  await expect(viewer.getByRole('columnheader', { name: 'Requirement' })).toBeVisible()
  await expect(viewer.getByRole('button', { name: '28', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(viewer.locator('.pdf-cell-mark')).toBeVisible()
})
