// Ползунок диапазона (components/RangeSlider.tsx): чистые помощники — ограничение и шаг значения, позиция на полосе в
// процентах и обратно, клавиши ←/→, PageUp/PageDown, Home/End, ручки не перескакивают друг через друга, разбор полей
// «от» / «до» и градиент полосы по зонам. Всё целочисленное с шагом step (по умолчанию 1).

/** Диапазон [от, до]; всегда от ≤ до */
export type Range = readonly [number, number]
/** Какая ручка: 0 — «от», 1 — «до» */
export type Handle = 0 | 1

export interface Bounds { min: number; max: number; step?: number; page?: number }

/** Значение в границах [lo, hi]; NaN — нижняя граница */
export const clamp = (v: number, lo: number, hi: number) => (Number.isNaN(v) ? lo : Math.min(hi, Math.max(lo, v)))

/** Ближайшее кратное шагу (от нуля); шаг 1 — целое */
export const snap = (v: number, step = 1) => {
  const s = step > 0 ? step : 1
  const r = Math.round(v / s) * s
  return Object.is(r, -0) ? 0 : r
}

/** Позиция значения на полосе, % от левого края (0…100) */
export const valueToPct = (v: number, min: number, max: number) => (max <= min ? 0 : clamp(((v - min) / (max - min)) * 100, 0, 100))

/** Значение по позиции на полосе (%): с шагом и в границах */
export const pctToValue = (pct: number, b: Bounds) =>
  clamp(snap(b.min + (clamp(pct, 0, 100) / 100) * (b.max - b.min), b.step), b.min, b.max)

/** Значение под указателем: x — координата указателя, left и width — полоса (getBoundingClientRect) */
export const pointerToValue = (x: number, left: number, width: number, b: Bounds) =>
  pctToValue(width > 0 ? ((x - left) / width) * 100 : 0, b)

/** Ручка переезжает в v, но не дальше соседней: «от» не больше «до», «до» не меньше «от» */
export function moveHandle(r: Range, h: Handle, v: number, b: Bounds): [number, number] {
  const x = snap(v, b.step)
  return h === 0 ? [clamp(x, b.min, r[1]), r[1]] : [r[0], clamp(x, r[0], b.max)]
}

/** Новое значение ручки по клавише: ←/↓ −шаг, →/↑ +шаг, PageDown/PageUp ∓10 шагов, Home — минимум, End — максимум;
 *  null — клавиша не наша (браузер обработает её сам) */
export function keyValue(key: string, v: number, b: Bounds): number | null {
  const step = b.step && b.step > 0 ? b.step : 1
  const page = b.page && b.page > 0 ? b.page : step * 10
  switch (key) {
    case 'ArrowLeft': case 'ArrowDown': return v - step
    case 'ArrowRight': case 'ArrowUp': return v + step
    case 'PageDown': return v - page
    case 'PageUp': return v + page
    case 'Home': return b.min
    case 'End': return b.max
    default: return null
  }
}

/** Диапазон после клавиши на ручке h; null — клавиша не наша */
export function keyMove(key: string, r: Range, h: Handle, b: Bounds): [number, number] | null {
  const v = keyValue(key, r[h], b)
  return v === null ? null : moveHandle(r, h, v, b)
}

/** Какую ручку тянуть при нажатии на полосу в точке v: ближайшую; ручки в одной точке — левее неё «от», иначе «до» */
export function nearestHandle(r: Range, v: number): Handle {
  if (r[0] === r[1]) return v < r[0] ? 0 : 1
  return Math.abs(v - r[0]) <= Math.abs(v - r[1]) ? 0 : 1
}

/** Текст поля → целое; пусто, не число или дробь — null. Пробелы по краям и знак «+» допускаются */
export function parseBound(text: string): number | null {
  const t = text.trim().replace(/^\+/, '')
  if (!/^-?\d+$/.test(t)) return null
  const n = Number(t)
  return Number.isSafeInteger(n) ? n : null
}

/** Что не так с текстом поля («от» — h 0, «до» — h 1) при текущем диапазоне; '' — годится как есть */
export function boundProblem(text: string, r: Range, h: Handle, b: Bounds): string {
  const n = parseBound(text)
  if (n === null) return `Введите целое число от ${b.min} до ${b.max}`
  if (n < b.min || n > b.max) return `Допустимо от ${b.min} до ${b.max}`
  if (h === 0 && n > r[1]) return `«от» — не больше ${r[1]}`
  if (h === 1 && n < r[0]) return `«до» — не меньше ${r[0]}`
  return ''
}

/** Применить текст поля по Enter или уходу из поля: целое — в границы и не дальше соседней ручки; не число — прежний диапазон */
export function commitBound(text: string, r: Range, h: Handle, b: Bounds): [number, number] {
  const n = parseBound(text)
  return n === null ? [r[0], r[1]] : moveHandle(r, h, clamp(n, b.min, b.max), b)
}

/** Диапазон без изменений — весь от min до max */
export const isFull = (r: Range, b: Pick<Bounds, 'min' | 'max'>) => r[0] <= b.min && r[1] >= b.max

/** «25–100» */
export const rangeText = (r: Range) => `${r[0]}–${r[1]}`

/** Полоса цвета: [from, to) значений */
export interface Band { from: number; to: number; color: string }

/** Градиент полосы по зонам: внутри зоны — её цвет, на границе — мягкий переход шириной blend (в значениях шкалы)
 *  поровну по обе стороны порога. Пустой список — прозрачная полоса */
export function bandGradient(bands: readonly Band[], min: number, max: number, blend = 0): string {
  if (!bands.length) return 'transparent'
  const at = (v: number) => `${+valueToPct(v, min, max).toFixed(2)}%`
  const half = Math.max(0, blend) / 2
  const stops: string[] = []
  bands.forEach((band, i) => {
    const a = i === 0 ? band.from : Math.min(band.from + half, band.to)
    const z = i === bands.length - 1 ? band.to : Math.max(band.to - half, a)
    stops.push(`${band.color} ${at(a)}`, `${band.color} ${at(z)}`)
  })
  return `linear-gradient(90deg, ${stops.join(', ')})`
}
