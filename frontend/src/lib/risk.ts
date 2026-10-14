import { ads as allAds, developers, keywordsDefault, keywordsSafe } from '../data/demo'
import type { Ad, Complex, RiskFactor, RiskResult, Zone } from '../data/types'

export const FUNDS_MARKERS = ['бронь', 'бронирование', 'первый взнос', 'взнос', 'рассрочка', 'ДДУ', 'долевого участия', 'от фундамента', 'цена котлована', 'напрямую застройщику', 'переуступка', 'фиксируем цену']
const SALE_MARKERS = ['продаж', 'продаём', 'продаем', 'продается', 'продаётся', 'квартир', 'бронь', 'цена', '₸']

const has = (t: string, list: string[]) => {
  const hits = list.filter((k) => t.toLowerCase().includes(k.toLowerCase()))
  // убираем вложенные совпадения: «взнос» внутри «первый взнос»
  return hits.filter((k) => !hits.some((o) => o !== k && o.toLowerCase().includes(k.toLowerCase())))
}

export function adSignals(ad: Ad) {
  const t = `${ad.text} ${ad.ocr ?? ''}`
  return {
    sale: has(t, SALE_MARKERS),
    funds: has(t, FUNDS_MARKERS),
    safe: has(t, keywordsSafe),
  }
}

export function scoreComplex(c: Complex, ads: Ad[] = allAds): RiskResult {
  const dev = developers.find((d) => d.id === c.developerId)
  const linked = ads.filter((a) => a.complexId === c.id && a.status !== 'dismissed')
  const built = c.stage === 'введён'
  const f: RiskFactor[] = []
  const b = c.basis

  // R1 — основание привлечения денег дольщиков
  const basisOk = b.guarantee === 'yes' || (b.mio === 'yes' && b.bvu === 'yes')
  const basisMissing = b.guarantee === 'no' && (b.mio === 'no' || b.bvu === 'no')
  f.push({
    code: 'R1', title: 'Нет основания для привлечения средств', points: 35,
    state: built || basisOk ? 'clear' : basisMissing ? 'hit' : 'unknown',
    evidence: built ? 'Объект введён в эксплуатацию — продажа допустима'
      : basisOk ? (b.guarantee === 'yes' ? `Есть гарантия ЕО${b.mio !== 'yes' ? ' (отсутствие отдельного разрешения МИО не является R1)' : ''}` : 'Разрешение МИО + БВУ')
      : basisMissing ? 'Гарантия ЕО отсутствует, разрешение МИО/БВУ не подтверждено'
      : 'Сведения не получены — 0 баллов, нужна проверка',
  })

  // R2 — реклама продажи на стадии строительства
  const saleAds = linked.filter((a) => { const s = adSignals(a); return s.sale.length > 0 && s.safe.length === 0 })
  f.push({
    code: 'R2', title: 'Реклама продажи на стадии строительства', points: 30,
    state: built ? 'clear' : saleAds.length ? 'hit' : 'clear',
    evidence: built ? 'Объект сдан' : saleAds.length ? `${saleAds.length} публ.: ${saleAds.map((a) => a.platform).join(', ')}` : 'Публикаций с признаками продажи нет',
  })

  // R3 — вне опубликованного перечня ГАСК
  f.push({
    code: 'R3', title: 'Отсутствует в опубликованном перечне', points: 10,
    state: built ? 'clear' : c.inPublishedList === 'no' ? 'hit' : c.inPublishedList === 'unknown' ? 'unknown' : 'clear',
    evidence: c.inPublishedList === 'yes' ? 'Есть в перечне от 01.10.2026' : c.inPublishedList === 'no' ? 'Нет в перечне от 01.10.2026 (само по себе не нарушение)' : 'Перечень не сверен',
  })

  // R4 — простой стройки
  f.push({
    code: 'R4', title: 'Простой строительства по снимкам', points: c.idleDays >= 90 ? 10 : 5,
    state: built ? 'clear' : c.photos.length === 0 ? 'unknown' : c.idleDays >= 30 ? 'hit' : 'clear',
    evidence: c.photos.length === 0 ? 'Снимков нет' : c.idleDays ? `Нет активности ${c.idleDays} дн.` : 'Активность зафиксирована',
  })

  // R5 — признаки привлечения денег в тексте
  const fundHits = [...new Set(linked.flatMap((a) => adSignals(a).funds))]
  f.push({
    code: 'R5', title: 'Признаки сбора денег в тексте', points: 10,
    state: built ? 'clear' : fundHits.length ? 'hit' : 'clear',
    evidence: fundHits.length ? fundHits.map((x) => `«${x}»`).join(', ') : 'Не найдено',
  })

  // R6 — профиль застройщика
  const devRisk = dev ? dev.unfinished > 0 && dev.objects <= 3 : false
  f.push({
    code: 'R6', title: 'Профиль застройщика', points: 10,
    state: devRisk ? 'hit' : 'clear',
    evidence: dev ? `${dev.objects} объектов, незавершённых ${dev.unfinished}` : '—',
  })

  const score = Math.min(100, f.filter((x) => x.state === 'hit').reduce((s, x) => s + x.points, 0))
  const r1 = f[0].state === 'hit', r2 = f[1].state === 'hit'
  const zone: Zone = score >= 60 && r1 && r2 ? 'red' : score >= 25 ? 'yellow' : 'green'
  const needsCheck = f.some((x) => x.state === 'unknown')
  const summary = zone === 'red'
    ? 'Подтверждены R1 и R2: реклама продажи без основания. Требуется решение аналитика.'
    : zone === 'yellow' ? 'Есть отклонения — рекомендуется проверка.' : needsCheck ? 'Нарушений не выявлено, но часть сведений не получена.' : 'Основания подтверждены, замечаний нет.'
  return { score, zone, needsCheck, factors: f, summary }
}

export function scoreAd(ad: Ad, complex?: Complex) {
  if (!ad.text.trim()) return { score: 0, zone: "green" as Zone, signals: adSignals(ad) }
  const s = adSignals(ad)
  let score = Math.min(40, s.sale.length * 10) + Math.min(30, s.funds.length * 10) - s.safe.length * 25
  if (complex) {
    const r = scoreComplex(complex)
    if (r.factors[0].state === 'hit') score += 30
    if (complex.stage === 'введён') score -= 30
  } else score += 10
  score = Math.max(0, Math.min(100, score))
  const zone: Zone = score >= 60 ? 'red' : score >= 30 ? 'yellow' : 'green'
  return { score, zone, signals: s }
}

export const allKeywords = [...keywordsDefault, ...FUNDS_MARKERS]

export const zoneLabel: Record<Zone, string> = { green: 'Зелёная', yellow: 'Жёлтая', red: 'Красная' }
