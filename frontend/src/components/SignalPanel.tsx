import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { Complex, Signal, SignalStatus } from '../data/types'
import { ApiError } from '../lib/api'
import { NOT_ASSESSED, isAssessed } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcFlag } from '../ui/icons'
import { fmtDT, fmtShort } from './ui'

const STATUS: Record<SignalStatus, { name: string; tag: string }> = {
  new: { name: 'Ждёт решения', tag: 'yellow' },
  confirmed: { name: 'Подтверждён', tag: 'red' },
  returned: { name: 'Возвращён на доработку', tag: 'gray' },
}
export const SignalStatusTag = ({ s }: { s: SignalStatus }) => <span className={`tag ${STATUS[s].tag}`}>{STATUS[s].name}</span>

/** Дата и время события сигнала с годом: «15 октября 2026, 20:14» или numeric — «15.10.2026 20:14» (время браузера) */
export function SignalTime({ at, numeric }: { at?: string | null; numeric?: boolean }) {
  if (!at) return <>—</>
  const d = new Date(at)
  const t = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  if (numeric) return <>{d.toLocaleDateString('ru-RU')} {t}</>
  return <>{d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).replace(' г.', '')}, {t}</>
}

type Load = { id: number; sig?: Signal; err?: unknown; at?: string }

/** Загружает сигнал по id и показывает понятные состояния: офлайн-копия, загрузка, не найден, ошибка.
 *  version — перечитать сигнал (после выгрузки PDF); при ошибке перечитывания остаётся прежняя версия.
 *  revision стора — перечитать после сохранённых изменений и после восстановления связи («Повторить» в баннере). */
export function SignalGate({ id, version = 0, children }: {
  id?: string; version?: number; children: (sig: Signal, ctl: { set: (s: Signal) => void; loadedAt: string }) => ReactNode
}) {
  const { mode, getSignal, revision } = useStore()
  const [res, setRes] = useState<Load | null>(null)
  const [retry, setRetry] = useState(0)
  const n = Number(id)
  const valid = Number.isInteger(n) && n > 0
  const ro = mode === 'snapshot'
  useEffect(() => {
    if (!valid || ro) return
    let alive = true
    getSignal(n).then(
      (sig) => { if (alive) setRes({ id: n, sig, at: new Date().toISOString() }) },
      (err: unknown) => { if (alive) setRes((r) => (r?.id === n && r.sig ? r : { id: n, err })) },
    )
    return () => { alive = false }
  }, [n, valid, ro, version, retry, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = (title: string, text: ReactNode, again?: boolean) => (
    <div className="empty">
      <div className="t-h3" style={{ color: 'var(--text)', marginBottom: 6 }}>{title}</div>
      <div>{text}</div>
      <div className="row" style={{ justifyContent: 'center', gap: 8, marginTop: 16 }}>
        {again && <button className="btn y m" onClick={() => { setRes(null); setRetry((x) => x + 1) }}>Повторить</button>}
        <Link to="/signals" className="btn g m">К реестру сигналов</Link>
      </div>
    </div>
  )
  if (!valid) return state('Сигнал не найден', `Нет сигнала с номером «${id ?? ''}».`)
  if (ro) return state('Сигнал открывается с сервера', <>Сейчас открыта офлайн-копия: в ней есть только реестр сигналов, без снимка доказательств, решений и PDF. Запустите <code>./dev.sh</code>, чтобы открыть сигнал.</>)
  if (res?.id !== n) return <div className="empty">Загружаем сигнал…</div>
  if (!res.sig) {
    const e = res.err
    if (e instanceof ApiError && e.status === 404) return state('Сигнал не найден', `Сигнала S-${String(n).padStart(4, '0')} нет — возможно, база была пересоздана.`)
    return state('Не удалось загрузить сигнал', e instanceof Error ? e.message : String(e), true)
  }
  return <>{children(res.sig, { set: (sig) => setRes({ id: n, sig, at: res.at }), loadedAt: res.at ?? res.sig.createdAt })}</>
}

/** Действие по сигналу для ЖК: открыть нерассмотренный сигнал или сформировать новый (снимок на сервере) */
export function SignalAction({ complex, label = 'Сформировать сигнал', compact }: { complex: Complex; label?: string; compact?: boolean }) {
  const { mode, signals, createSignal, refreshSignals, notify, riskOf } = useStore()
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  // карточка из открытого реестра без проверенных сведений: риска нет — и сигнала (сервер тоже откажет, 422)
  const scored = isAssessed(riskOf(complex))
  const open = signals.find((s) => s.complexId === complex.id && s.status === 'new')
  const size = compact ? 'btn m full' : 'btn full'

  if (open) return (
    <div>
      <Link to={`/signals/${open.id}`} className={`${size} ${compact ? 'g' : 'y'}`}><IcFlag />Открыть сигнал {open.code}</Link>
      <div className="t-xs c2" style={{ marginTop: 6 }}>Ждёт решения · создан {fmtDT(open.createdAt)}</div>
    </div>
  )

  const create = async () => {
    setBusy(true)
    try {
      const s = await createSignal(complex.id)
      notify(`Сигнал ${s.code} сформирован`)
      nav(`/signals/${s.id}`)
    } catch (e) {
      const id = e instanceof ApiError && e.status === 409 ? (e.data as { signalId?: unknown } | undefined)?.signalId : undefined
      if (typeof id === 'number') {
        void refreshSignals() // ошибку обновления реестра стор покажет баннером
        notify(e instanceof Error ? e.message : 'Сигнал по объекту уже есть')
        nav(`/signals/${id}`)
      } else notify(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div>
      <button className={`${size} ${compact ? 'g' : 'y'}`} disabled={busy || mode !== 'api' || !scored} onClick={create}><IcFlag />{busy ? 'Формируем…' : label}</button>
      {!scored && <div className="t-xs c2" style={{ marginTop: 6 }}>{NOT_ASSESSED} — сигнал не формируется</div>}
      {scored && mode === 'snapshot' && <div className="t-xs c2" style={{ marginTop: 6 }}>Сигналы формируются на сервере — в офлайн-копии недоступно (запустите ./dev.sh)</div>}
    </div>
  )
}

/** Блок «Сигнальная карточка» в карточке ЖК: сформировать сигнал / открыть текущий, история сигналов по объекту */
export default function SignalPanel({ complex }: { complex: Complex }) {
  const { signals } = useStore()
  const past = signals.filter((s) => s.complexId === complex.id && s.status !== 'new')
  return (
    <div className="sigpanel">
      <SignalAction complex={complex} />
      <p className="t-xs c2" style={{ marginTop: 10, lineHeight: '17px' }}>Сигнал — снимок риска и доказательств на момент создания. Решение по нему принимает аналитик.</p>
      {past.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="t-sm c2" style={{ marginBottom: 2 }}>Сигналы по объекту</div>
          {past.slice(0, 5).map((s) => (
            <Link key={s.id} to={`/signals/${s.id}`} className="sigrow">
              <span className="bold num">{s.code}</span><SignalStatusTag s={s.status} /><span className="t-xs c2" style={{ marginLeft: 'auto' }}>{fmtShort(s.createdAt)}</span>
            </Link>
          ))}
          {past.length > 5 && <Link to={`/signals?complex=${complex.id}`} className="link t-sm">Все сигналы по объекту · {past.length}</Link>}
        </div>
      )}
    </div>
  )
}
