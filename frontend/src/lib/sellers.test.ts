import { describe, expect, it } from 'vitest'
import { belowText, fmtPerM2, nodeLabel, phoneHint, radialLayout, ROOMS_LABEL, type GraphEdge, type GraphNode } from './sellers'

describe('цена', () => {
  it('отклонение от медианы словами', () => {
    expect(belowText(38)).toBe('ниже рынка на 38%')
    expect(belowText(-12)).toBe('выше рынка на 12%')
    expect(belowText(2)).toBe('на уровне рынка')
    expect(belowText(null)).toBe('нет медианы')
  })
  it('формат цены за м² и комнаты', () => {
    expect(fmtPerM2(470000)).toBe('470 000 ₸/м²')
    expect(fmtPerM2(null)).toBe('—')
    expect([ROOMS_LABEL('0'), ROOMS_LABEL(2), ROOMS_LABEL('4'), ROOMS_LABEL(null)]).toEqual(['студии', '2-комн.', '4+ комн.', 'не указано'])
  })
})

describe('подпись узла', () => {
  it('без префикса Krisha и с обрезкой', () => {
    expect(nodeLabel('krisha.kz / специалист · AD-1FD95B8E')).toBe('специалист · AD-1FD95B8E')
    expect(nodeLabel('@очень.длинное.имя.аккаунта.застройщика', 12)).toBe('@очень.длин…')
  })
})

describe('подсказка номера', () => {
  it('сколько ещё публикаций и аккаунтов', () => {
    expect(phoneHint({ otherAds: 8, accounts: 2 })).toBe('Этот номер встречается ещё в 8 публикациях / 2 аккаунтах')
    expect(phoneHint({ otherAds: 1, accounts: 1 })).toBe('Этот номер встречается ещё в 1 публикации / 1 аккаунте')
    expect(phoneHint({ otherAds: 21, accounts: 3 })).toBe('Этот номер встречается ещё в 21 публикации / 3 аккаунтах')
    expect(phoneHint({ otherAds: 0, accounts: 1 })).toBe('В других публикациях этот номер не встречается')
  })
})

describe('радиальная схема', () => {
  const nodes: GraphNode[] = [
    { id: 'instagram:@a', kind: 'account', label: '@a', weight: 3 },
    { id: 'tiktok:@a', kind: 'account', label: '@a', weight: 1 },
    { id: 'instagram:@b', kind: 'account', label: '@b', weight: 1 },
    { id: 'phone:7771112233', kind: 'phone', label: '+7 777 111 22 33', weight: 2 },
    { id: 'complex:c1', kind: 'complex', label: 'Альфа', weight: 1 },
  ]
  const edges: GraphEdge[] = [
    { a: 'instagram:@a', b: 'phone:7771112233', kind: 'phone' }, { a: 'instagram:@b', b: 'phone:7771112233', kind: 'phone' },
    { a: 'instagram:@a', b: 'tiktok:@a', kind: 'handle' }, { a: 'instagram:@a', b: 'complex:c1', kind: 'complex' },
  ]
  it('телефон в центре, аккаунты на кольце, ЖК снаружи — в пределах полотна и детерминированно', () => {
    const p = radialLayout(nodes, edges, 600, 400)
    const at = (id: string) => p.find((n) => n.id === id)!
    expect(p).toHaveLength(5)
    expect([at('phone:7771112233').x, at('phone:7771112233').y]).toEqual([300, 200])
    const d = (id: string) => Math.hypot(at(id).x - 300, at(id).y - 200)
    expect(d('complex:c1')).toBeGreaterThan(d('instagram:@a'))
    expect(p.every((n) => n.x >= 0 && n.x <= 600 && n.y >= 0 && n.y <= 400)).toBe(true)
    expect(radialLayout(nodes, edges, 600, 400)).toEqual(p)
  })
  it('один аккаунт без контактов — в центре', () => {
    const p = radialLayout([nodes[0]], [], 300, 300)
    expect([p[0].x, p[0].y]).toEqual([150, 150])
  })
})
