import { describe, expect, it } from 'vitest'
import { locationLabel, parseEvent } from './api'

describe('source coordinates', () => {
  it('keeps a real page range and section', () => {
    expect(locationLabel({ start_page: 2, end_page: 4, section_path: ['Лаборатория', 'Проверка'], blocks: [] }))
      .toBe('Стр. 2–4 · Лаборатория › Проверка')
  })
  it('does not invent Word pages', () => {
    expect(locationLabel({ paragraph_index: 3, end_paragraph_index: 3, blocks: [] })).toBe('Абз. 3')
  })
  it('returns empty without source coordinates', () => {
    expect(locationLabel({ blocks: [] })).toBe('')
  })
})
describe('stream frames', () => {
  it('parses CRLF and multiple data fields', () => {
    expect(parseEvent('event: token\r\ndata: {\r\ndata: "text": "фрагмент"}')).toEqual({ kind: 'token', data: { text: 'фрагмент' } })
  })
  it('ignores SSE keepalive', () => {
    expect(parseEvent(': ping')).toBeNull()
  })
})
