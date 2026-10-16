import { describe, expect, it } from 'vitest'
import { KNOWN_PLATFORMS, PLATFORMS, externalUrl, platformName } from './platform'

describe('platformName — подпись площадки по коду', () => {
  it('известные площадки — их название', () => {
    expect(platformName('instagram')).toBe('Instagram')
    expect(platformName('krisha')).toBe('Krisha.kz')
    expect(platformName('2gis')).toBe('2ГИС')
  })

  it("'other', неизвестный код и пусто — «Другая площадка»", () => {
    expect(platformName('other')).toBe('Другая площадка')
    expect(platformName('vk')).toBe('Другая площадка')
    expect(platformName('')).toBe('Другая площадка')
    expect(platformName(null)).toBe('Другая площадка')
    expect(platformName(undefined)).toBe('Другая площадка')
  })

  it('свойства прототипа — не площадки', () => {
    expect(platformName('toString')).toBe('Другая площадка')
    expect(platformName('__proto__')).toBe('Другая площадка')
    expect(platformName('constructor')).toBe('Другая площадка')
  })
})

describe('KNOWN_PLATFORMS — выбор площадки при ручном вводе', () => {
  it('все площадки таблицы, кроме «Другой площадки», в порядке таблицы', () => {
    expect(KNOWN_PLATFORMS).toEqual(['instagram', 'tiktok', 'krisha', 'olx', 'telegram', 'facebook', '2gis'])
    expect(KNOWN_PLATFORMS).not.toContain('other')
    expect(Object.keys(PLATFORMS)).toEqual([...KNOWN_PLATFORMS, 'other'])
  })
})

describe('externalUrl — ссылка на публикацию в источнике', () => {
  it('http(s) — нормализованный адрес; пробелы по краям обрезаются', () => {
    expect(externalUrl('https://www.instagram.com/p/Cx1/')).toBe('https://www.instagram.com/p/Cx1/')
    expect(externalUrl('  http://krisha.kz/a/show/1  ')).toBe('http://krisha.kz/a/show/1')
    expect(externalUrl('HTTPS://OLX.KZ/d/obyavlenie')).toBe('https://olx.kz/d/obyavlenie')
  })

  it('опасные и прочие схемы — нет ссылки', () => {
    expect(externalUrl('javascript:alert(1)')).toBeUndefined()
    expect(externalUrl('JaVaScRiPt:alert(1)')).toBeUndefined()
    expect(externalUrl(' javascript:alert(1)')).toBeUndefined()
    expect(externalUrl('data:text/html,<script>1</script>')).toBeUndefined()
    expect(externalUrl('vbscript:msgbox(1)')).toBeUndefined()
    expect(externalUrl('file:///etc/passwd')).toBeUndefined()
    expect(externalUrl('tg://resolve?domain=x')).toBeUndefined()
  })

  it('не адрес, относительный путь и пусто — нет ссылки', () => {
    expect(externalUrl('instagram.com/p/Cx1')).toBeUndefined()
    expect(externalUrl('/ads/a1')).toBeUndefined()
    expect(externalUrl('//evil.example/x')).toBeUndefined()
    expect(externalUrl('   ')).toBeUndefined()
    expect(externalUrl('')).toBeUndefined()
    expect(externalUrl(null)).toBeUndefined()
    expect(externalUrl(undefined)).toBeUndefined()
  })
})
