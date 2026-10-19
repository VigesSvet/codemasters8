// «Удалено в источнике» и «История изменений» в разборе публикации: статус в источнике по сборам (app/changes.py) и
// пословный diff соседних версий — удалённое красным, добавленное зелёным. Нет связи с сервером — блоков нет.
import { useEffect, useState } from 'react'
import type { AdVersion } from '../data/types'
import { errorText } from '../lib/api'
import { changed, ddmm, diffWords, getSourceStatus, type DiffPart, type SourceStatus } from '../lib/changes'
import { getVersions } from '../lib/connectors'
import { fmtDT } from './ui'

const DEL: React.CSSProperties = { background: 'var(--red-bg)', color: 'var(--red-ink)', textDecoration: 'line-through', borderRadius: 4, marginRight: 2 }
const ADD: React.CSSProperties = { background: 'var(--green-bg)', color: 'var(--green-ink)', borderRadius: 4 }

function useStatus(adId: string) {
  const [s, setS] = useState<{ id: string; v: SourceStatus | null } | null>(null)
  useEffect(() => {
    let alive = true
    getSourceStatus(adId).then((v) => { if (alive) setS({ id: adId, v }) }, () => { /* необязательные сведения */ })
    return () => { alive = false }
  }, [adId])
  return s?.id === adId ? s.v : null
}

/** Бейдж «Удалено в источнике · копия сохранена ДД.ММ» — только у удалённых; остальное — в «Истории изменений» */
export function DeletedBadge({ adId }: { adId: string }) {
  const s = useStatus(adId)
  if (s?.status !== 'deleted') return null
  return <span className="tag red" title={s.how}>Удалено в источнике{s.savedAt ? ` · копия сохранена ${ddmm(s.savedAt)}` : ''}</span>
}

export function StatusLine({ s }: { s: SourceStatus }) {
  const tone = s.status === 'deleted' ? 'red' : s.status === 'present' ? 'green' : 'gray'
  const label = s.status === 'deleted' ? `Удалено в источнике${s.savedAt ? ` · копия сохранена ${ddmm(s.savedAt)}` : ''}`
    : s.status === 'present' ? 'Есть в источнике' : 'Наличие в источнике не определено'
  return (
    <div className="row wrap-f" style={{ gap: 8 }}>
      <span className={`tag ${tone}`}>{label}</span>
      <span className="t-sm c2">{s.how}{s.checkedAt ? ` · проверено по сбору ${fmtDT(s.checkedAt)}` : ''}{s.status !== 'present' && s.lastSeenAt ? ` · последний раз видели ${fmtDT(s.lastSeenAt)}` : ''}</span>
    </div>
  )
}

export function Diff({ parts }: { parts: DiffPart[] }) {
  return (
    <div className="t-sm" style={{ lineHeight: '20px', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 220, overflowY: 'auto' }}>
      {parts.map(([op, t], i) => op === 'eq' ? <span key={i}>{t}</span>
        : op === 'del' ? <del key={i} style={DEL} title="убрано в новой версии">{t}</del>
          : <ins key={i} style={{ ...ADD, textDecoration: 'none' }} title="добавлено в новой версии">{t}</ins>)}
    </div>
  )
}

/** «История изменений»: статус в источнике и что поменялось между версиями (новые первыми) */
export function ChangeHistory({ adId, version }: { adId: string; version: number }) {
  const s = useStatus(adId)
  const key = `${adId}:${version}`
  const [load, setLoad] = useState<{ key: string; vs?: AdVersion[] | null; err?: string } | null>(null)
  useEffect(() => {
    let alive = true
    getVersions(adId).then((vs) => { if (alive) setLoad({ key, vs }) }, (e: unknown) => { if (alive) setLoad({ key, err: errorText(e) }) })
    return () => { alive = false }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const mine = load?.key === key ? load : null
  const vs = mine ? mine.vs : undefined
  const err = mine?.err ?? null
  if (!s && vs === null) return null // офлайн-копия или сервер без сбора
  const asc = [...(vs ?? [])].sort((a, b) => a.version - b.version)
  const pairs = asc.slice(1).map((cur, k) => ({ prev: asc[k], cur, diff: diffWords(asc[k].text, cur.text) })).reverse()
  return (
    <div className="card">
      <div className="card-head"><h3>История изменений</h3>{asc.length > 0 && <span className="t-sm c2">{asc.length} {asc.length === 1 ? 'версия' : asc.length < 5 ? 'версии' : 'версий'} в источнике</span>}</div>
      {s && <StatusLine s={s} />}
      {err && <div className="alog-err t-sm" style={{ marginTop: 8 }}>Версии не загрузились: {err}</div>}
      {vs === undefined && !err ? <div className="t-sm c2" style={{ marginTop: 8 }}>Загружаем версии…</div>
        : !pairs.length ? <div className="t-sm c2" style={{ marginTop: 8 }}>{asc.length ? 'Изменений в источнике не зафиксировано: сохранена одна версия.' : 'Версий нет: публикация не из приёма.'}</div>
          : pairs.map(({ prev, cur, diff }) => (
            <div key={cur.version} style={{ padding: '12px 0 4px', borderTop: '1px solid var(--line)', marginTop: 12 }}>
              <div className="row wrap-f" style={{ gap: 8, marginBottom: 6 }}>
                <span className="bold">Версия {prev.version} → {cur.version}</span>
                <span className="t-sm c2">{cur.collectedAt ? `получена ${fmtDT(cur.collectedAt)}` : 'время не указано'}</span>
                {(prev.price ?? null) !== (cur.price ?? null) && <span className="tag yellow">Цена: {prev.price || '—'} → {cur.price || '—'}</span>}
                {[...prev.mediaKeys].sort().join() !== [...cur.mediaKeys].sort().join() && <span className="tag gray">файлы изменились</span>}
              </div>
              {changed(diff) ? <Diff parts={diff} /> : <div className="t-sm c2">Текст не менялся</div>}
            </div>
          ))}
      <div className="t-xs c3" style={{ marginTop: 8 }}>Копия каждой версии хранится на сервере. Удаление определяется по отсутствию в сборах аккаунта — это признак, а не подтверждение: сверьте по ссылке.</div>
    </div>
  )
}
