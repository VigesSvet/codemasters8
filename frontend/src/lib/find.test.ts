import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { Complex, Developer, RegistryObject } from '../data/types'
import { adHit, adKey, codeQuery, finder, findComplex, findObject, rankComplex, rankObject } from './find'

const c = { name: 'Нұр Әлем', address: 'ул. Шакарима, 88', district: 'Центр' } as Complex
const dev = { name: 'ТОО «Ертіс Строй Инвест»', bin: '180840012345' } as Developer
const o = { name: 'МЖК "Renesans"', address: 'г. Усть-Каменогорск, пр. И. Есенберлина, д. 20/1', developer: 'ТОО "Mercury Construction"', developerBin: '220740026935' } as RegistryObject

// выдача по демо-набору и открытым данным — как в шапке: точные совпадения, а по скелету — только когда точных нет
const read = <T>(name: string): T => JSON.parse(readFileSync(fileURLToPath(new URL(`../../../shared/${name}`, import.meta.url)), 'utf-8')) as T
const seed = read<{ complexes: Complex[]; developers: Developer[] }>('seed_data.json')
const reg = read<{ objects: RegistryObject[]; withoutCoords: RegistryObject[] }>('registry_ust_kamenogorsk.json')
const rows = seed.complexes.map((x) => ({ c: x, dev: seed.developers.find((d) => d.id === x.developerId) }))
const objects = [...reg.objects, ...reg.withoutCoords]
const cx = (q: string) => rows.filter(finder(q, rows, (q, x) => rankComplex(q, x.c, x.dev))).map((x) => x.c.name)
const od = (q: string) => objects.filter(finder(q, objects, rankObject)).map((x) => x.name)

describe('поиск ЖК и открытых данных', () => {
  it('ЖК — по названию, адресу, району, застройщику и БИН', () => {
    for (const q of ['Нур Алем', 'nur alem', 'ЖК Нұр Әлем', 'жк«Нур Алем»', 'Шакарима 88', 'центр', 'Ертис', '180840012345']) expect(findComplex(q, c, dev), q).toBe(true)
    expect(findComplex('Сатпаева', c, dev)).toBe(false)
  })
  it('открытые данные — название без «МЖК», адрес, застройщик, БИН', () => {
    for (const q of ['Ренесанс', 'renesans', 'МЖК Renesans', 'Есенберлина 20', 'mercury', '220740026935']) expect(findObject(q, o), q).toBe(true)
    expect(findObject('Жаным', o)).toBe(false)
  })
  it('транслит и казахские буквы находят нужный ЖК', () => {
    expect(cx('Shygys')).toEqual(['Шығыс Тауэр', 'Солнечный квартал'])
    expect(cx('Шыгыс')).toEqual(cx('Shygys'))
    expect(cx('shygys tower')).toEqual(['Шығыс Тауэр'])
    expect(cx('Altai Residence')).toEqual(['Алтай Резиденс'])
    expect(cx('Нур Алем')).toEqual(['Нұр Әлем'])
    expect(cx('Сатпаева')).toEqual(['Шығыс Тауэр', 'Тихий берег'])
  })
  it('скелет не ловит случайное: есть точные совпадения — только они; по адресу — только как есть', () => {
    // Береке Сити и Green Line — оба у ТОО «Береке Девелопмент»
    expect(cx('Береке')).toEqual(['Береке Сити', 'Green Line'])
    expect(cx('Ертис')).toEqual(['Ертіс Парк', 'Нұр Әлем'])
    expect(rankComplex('Ертис', rows.find((x) => x.c.id === 'c3')!.c, rows.find((x) => x.c.id === 'c3')!.dev)).toBe(0)
    // «Кокжал Барака» — адрес ASYLTAS, ASYLYM, Orda, Diamond, «Престижа»: по скелету «Береке» их не находит
    expect(od('Береке')).toEqual([])
    expect(od('Кокжал Барака').length).toBe(5)
    expect(od('Ертис')).toEqual([])
  })
  it('пустой запрос — всё', () => {
    expect(cx('').length).toBe(seed.complexes.length)
    expect(od(' ').length).toBe(objects.length)
  })
})

describe('номера публикаций и сигналов', () => {
  it('демо — по числу, добавленные вручную — весь id: с демо не пересекаются', () => {
    expect(adKey('a7')).toBe('AD-007')
    expect(adKey('a12')).toBe('AD-012')
    expect(adKey('m1a2b3c4')).toBe('AD-M1A2B3C4')
    expect(adKey('mgx3k2p1')).toBe('AD-MGX3K2P1')
    expect(adKey('m004')).not.toBe(adKey('a4'))
  })
  it('коды в запросе', () => {
    expect(codeQuery('AD-7')).toEqual({ kind: 'ad', key: 'AD-007' })
    expect(codeQuery('ад 007')).toEqual({ kind: 'ad', key: 'AD-007' })
    expect(codeQuery('AD-M1A2B3C4')).toEqual({ kind: 'ad', key: 'AD-M1A2B3C4' })
    expect(codeQuery('ad-mgxkq')).toEqual({ kind: 'ad', key: 'AD-MGXKQ' })
    expect(codeQuery('ad m1a2')).toEqual({ kind: 'ad', key: 'AD-M1A2' })
    expect(codeQuery('S-0001')).toEqual({ kind: 'signal', n: 1 })
    expect(codeQuery('s1')).toEqual({ kind: 'signal', n: 1 })
    expect(codeQuery('ad group')).toBeNull()
    expect(codeQuery('Сатпаева 14')).toBeNull()
    expect(codeQuery('230240077003')).toBeNull()
  })
  it('публикация под номер: демо — целиком, ручная — и по началу', () => {
    const hit = (q: string, id: string) => { const k = codeQuery(q); return k?.kind === 'ad' && adHit(k.key, id) }
    expect(hit('AD-7', 'a7')).toBe(true)
    expect(hit('AD-1', 'a12')).toBe(false)
    expect(hit('AD-4', 'm004')).toBe(false)
    expect(hit('ad-m1a2b3c4', 'm1a2b3c4')).toBe(true)
    expect(hit('AD-M1A', 'm1a2b3c4')).toBe(true)
    expect(hit('AD-M1B', 'm1a2b3c4')).toBe(false)
  })
})
