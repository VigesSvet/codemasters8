// Ползунок диапазона: поля «от» / «до» и полоса-градиент с двумя ручками под ней (мышь, касание, клавиатура).
// Ручки тянутся с шагом step и не перескакивают друг через друга; часть полосы вне диапазона приглушена.
// Расчёты — в lib/range.ts (там же тесты).
import { useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import {
  boundProblem, commitBound, keyMove, moveHandle, nearestHandle, pointerToValue, valueToPct, type Bounds, type Handle, type Range,
} from '../lib/range'

interface Props {
  value: Range
  onChange: (v: [number, number]) => void
  min?: number
  max?: number
  step?: number
  /** Шаг PageUp / PageDown; по умолчанию — 10 шагов */
  page?: number
  /** Фон полосы (CSS: цвет или градиент) */
  track?: string
  /** Подписи под полосой */
  ticks?: readonly number[]
  /** Названия ручек для экранного диктора */
  labels?: readonly [string, string]
  /** Подписи полей */
  fields?: readonly [string, string]
  /** Значение словами для экранного диктора: «25 баллов» */
  valueText?: (v: number) => string
}

/** Что тянут: ручку h; pending — ручки в одной точке, какую тянуть, решает первое движение (влево — «от», вправо — «до») */
type Drag = { h: Handle } | { pending: Handle; x: number }

export default function RangeSlider({
  value, onChange, min = 0, max = 100, step = 1, page, track, ticks = [], labels = ['Минимум', 'Максимум'], fields = ['от', 'до'], valueText,
}: Props) {
  const b: Bounds = { min, max, step, page }
  const uid = useId()
  const trackRef = useRef<HTMLDivElement>(null)
  const h0 = useRef<HTMLDivElement>(null), h1 = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const [dragging, setDragging] = useState<Handle | null>(null)
  // черновики полей: null — поле показывает значение; текст — пока его правят
  const [draft, setDraft] = useState<[string | null, string | null]>([null, null])

  // во время перетаскивания вторая ручка стоит на месте — диапазон из последней отрисовки годится для ограничения
  const emit = (next: [number, number]) => { if (next[0] !== value[0] || next[1] !== value[1]) onChange(next) }
  const at = (clientX: number) => {
    const rc = trackRef.current?.getBoundingClientRect()
    return rc ? pointerToValue(clientX, rc.left, rc.width, b) : null
  }
  const focus = (h: Handle) => (h ? h1 : h0).current?.focus({ preventScroll: true })
  const grab = (h: Handle) => { drag.current = { h }; setDragging(h); focus(h) }

  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    const v = at(e.clientX)
    if (v === null) return
    e.preventDefault()
    // захват — чтобы ручка шла за указателем и за пределами полосы; без него тянуть можно в её пределах
    try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* указатель уже отпущен */ }
    const hit = (e.target as HTMLElement).closest<HTMLElement>('[data-handle]')?.dataset.handle
    if (hit === '0' || hit === '1') {
      const h: Handle = hit === '1' ? 1 : 0
      if (value[0] === value[1]) drag.current = { pending: h, x: e.clientX }
      else grab(h)
      return
    }
    // нажатие на полосу (не на ручку) сразу переносит ближайшую ручку в эту точку, дальше её можно тянуть
    const h = nearestHandle(value, v)
    grab(h)
    emit(moveHandle(value, h, v, b))
  }
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d) return
    if ('pending' in d) {
      if (Math.abs(e.clientX - d.x) < 2) return
      grab(e.clientX < d.x ? 0 : 1)
    }
    const g = drag.current
    const v = at(e.clientX)
    if (v !== null && g && 'h' in g) emit(moveHandle(value, g.h, v, b))
  }
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d) return
    if ('pending' in d) focus(d.pending)
    drag.current = null
    setDragging(null)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }
  const onKey = (h: Handle) => (e: KeyboardEvent<HTMLElement>) => {
    const next = keyMove(e.key, value, h, b)
    if (!next) return
    e.preventDefault()
    emit(next)
  }

  const clearDraft = (h: Handle) => setDraft((d) => (h ? [d[0], null] : [null, d[1]]))
  const setField = (h: Handle, text: string) => {
    setDraft((d) => (h ? [d[0], text] : [text, d[1]]))
    // правильное значение применяется сразу; неправильное — подсказка под полями, а по Enter или уходу из поля — ближайшее допустимое
    if (!boundProblem(text, value, h, b)) emit(commitBound(text, value, h, b))
  }
  const commitField = (h: Handle) => {
    const text = draft[h]
    if (text === null) return
    emit(commitBound(text, value, h, b))
    clearDraft(h)
  }
  const fieldKey = (h: Handle) => (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') { e.preventDefault(); commitField(h) } else if (e.key === 'Escape') clearDraft(h)
    else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault()
      clearDraft(h)
      const next = keyMove(e.key, value, h, b)
      if (next) emit(next)
    }
  }
  const problems = [draft[0] === null ? '' : boundProblem(draft[0], value, 0, b), draft[1] === null ? '' : boundProblem(draft[1], value, 1, b)]
  const problem = problems[0] || problems[1]
  const errId = `${uid}-err`
  const field = (h: Handle) => (
    <label className={`rs-field ${problems[h] ? 'bad' : ''}`}>
      <span>{fields[h]}</span>
      <input value={draft[h] ?? String(value[h])} inputMode="numeric" autoComplete="off" spellCheck={false} maxLength={4}
        aria-label={`${labels[h]}, ${fields[h]}`} aria-invalid={!!problems[h]} aria-describedby={problems[h] ? errId : undefined}
        onChange={(e) => setField(h, e.target.value)} onBlur={() => commitField(h)} onKeyDown={fieldKey(h)} />
    </label>
  )

  const p0 = valueToPct(value[0], min, max), p1 = valueToPct(value[1], min, max)
  const handle = (h: Handle) => (
    <div ref={h ? h1 : h0} data-handle={h} className={`rs-h ${dragging === h ? 'on' : ''}`} style={{ left: `${h ? p1 : p0}%` }}
      role="slider" tabIndex={0} aria-label={labels[h]} aria-orientation="horizontal"
      aria-valuemin={h ? value[0] : min} aria-valuemax={h ? max : value[1]} aria-valuenow={value[h]}
      aria-valuetext={valueText ? valueText(value[h]) : undefined} onKeyDown={onKey(h)}>
      <span className="rs-line" /><span className="rs-caret" />
    </div>
  )

  return (
    <div className={`rs ${dragging !== null ? 'drag' : ''}`}>
      <div className="rs-fields">{field(0)}<span className="rs-dash" aria-hidden>–</span>{field(1)}</div>
      {problem && <div className="rs-err" id={errId} role="status">{problem}</div>}
      <div className="rs-rail" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
        <div className="rs-track" ref={trackRef}>
          <div className="rs-bar" style={{ background: track }}>
            <span className="rs-dim" style={{ left: 0, width: `${p0}%` }} />
            <span className="rs-dim" style={{ left: `${p1}%`, right: 0 }} />
            <span className="rs-sel" style={{ left: `${p0}%`, width: `${p1 - p0}%` }} />
          </div>
          {handle(0)}{handle(1)}
        </div>
      </div>
      {ticks.length > 0 && (
        <div className="rs-ticks" aria-hidden>
          {ticks.map((t) => (
            <span key={t} className="num" style={{ left: `${valueToPct(t, min, max)}%` }}>{t}</span>
          ))}
        </div>
      )}
    </div>
  )
}
