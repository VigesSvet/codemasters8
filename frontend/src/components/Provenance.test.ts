import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SrcLink } from './Provenance'

const html = (url: string, label?: string) => renderToStaticMarkup(createElement(SrcLink, { url }, label)).replace(/<!-- -->/g, '')

describe('SrcLink - ссылка на источник', () => {
  it('http(s) - внешняя ссылка в новой вкладке', () => {
    const s = html('https://homeportal.kz/ru/projects/953', 'страница источника')
    expect(s).toMatch(/^<a class="link prov-a" href="https:\/\/homeportal\.kz\/ru\/projects\/953" target="_blank" rel="noreferrer">страница источника<svg/)
    expect(html(' http://elicense.kz ')).toContain('href="http://elicense.kz/"')
  })

  // адрес из источника или от жителя не исполняется: вместо ссылки - подпись текстом
  it.each(['javascript:alert(1)', ' JaVaScRiPt:alert(1)', 'data:text/html,<script>1</script>', 'vbscript:msgbox(1)', 'file:///etc/passwd',
    'satti.kz', '//evil.example/x', ''])('"%s" - без ссылки, подпись остаётся', (url) => {
    const s = html(url, 'satti.kz')
    expect(s).not.toMatch(/<a\b|href=/)
    expect(s).toBe('<span class="prov-a">satti.kz</span>')
    expect(html(url)).toBe('<span class="prov-a">источник</span>')
  })
})
