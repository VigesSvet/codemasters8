// /changes — изменения собранных публикаций (цена, условия оплаты, текст) и удалённые в источнике — GET /api/changes.
// Удаление — признак по отсутствию в сборах аккаунта, не подтверждение; копия публикации остаётся на сервере.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { PageTitle, PlatformIcon, fmtDT } from '../components/ui'
import type { Platform } from '../data/types'
import { errorText } from '../lib/api'
import { KIND_LABEL, ddmm, getChanges, type ChangesOut } from '../lib/changes'
import { platformName } from '../lib/platform'
import { useStore } from '../lib/store'

const DAYS = [7, 30, 90, 365] as const
const DEL: React.CSSProperties = { background: 'var(--red-bg)', color: 'var(--red-ink)', borderRadius: 4, marginRight: 2 }
const ADD: React.CSSProperties = { background: 'var(--green-bg)', color: 'var(--green-ink)', textDecoration: 'none', borderRadius: 4 }

export default function Changes() {
  const { complexes } = useStore()
  const [days, setDays] = useState<number>(30)
  const [load, setLoad] = useState<{ days: number; d?: ChangesOut | null; err?: string } | null>(null)
  useEffect(() => {
    let alive = true
    getChanges(days).then((d) => { if (alive) setLoad({ days, d }) }, (e: unknown) => { if (alive) setLoad({ days, err: errorText(e) }) })
    return () => { alive = false }
  }, [days])
  const mine = load?.days === days ? load : null
  const d = mine ? mine.d : undefined
  const err = mine?.err ?? null
  const cx = (id?: string | null) => (id ? complexes.find((c) => c.id === id)?.name : undefined)
  const who = (x: { platform: string; account: string }) => <span className="row" style={{ gap: 6 }}><PlatformIcon p={x.platform as Platform} size={18} />{platformName(x.platform)} · {x.account || 'аккаунт не указан'}</span>

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Изменения в источниках']]} title="Изменения и удалённые публикации"
        sub="Что поменялось в собранной рекламе между сборами и что исчезло из источника — копии сохранены на сервере" />
      <div className="row wrap-f" style={{ gap: 8, marginBottom: 16 }}>
        <div className="seg">{DAYS.map((n) => <button key={n} className={days === n ? 'on' : ''} onClick={() => setDays(n)}>{n} дн.</button>)}</div>
        {d && <span className="t-sm c2">Публикаций в приёме: {d.items} · есть в источнике {d.sourceStatus.present ?? 0} · удалено {d.sourceStatus.deleted ?? 0} · не определено {d.sourceStatus.unknown ?? 0}</span>}
      </div>
      {err && <div className="alog-err t-sm">Изменения не загрузились: {err}</div>}
      {d === undefined && !err && <div className="t-sm c2">Загружаем…</div>}
      {d === null && <div className="ai-note">Лента изменений доступна с сервером: в офлайн-копии версий публикаций нет.</div>}
      {d && (
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="card-head"><h3>Удалено в источнике</h3><span className="t-sm c2">{d.deleted.length}</span></div>
            {!d.deleted.length ? <div className="t-sm c2">Удалённых публикаций не найдено: все собранные Instagram и TikTok есть в последних сборах своих аккаунтов или вытеснены из окна сбора новыми.</div>
              : d.deleted.map((x) => (
                <div key={x.adId} style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
                  <div className="row wrap-f" style={{ gap: 8 }}>
                    <span className="tag red">Удалено в источнике{x.savedAt ? ` · копия сохранена ${ddmm(x.savedAt)}` : ''}</span>
                    {who(x)}{cx(x.complexId) && <span className="tag gray">ЖК «{cx(x.complexId)}»</span>}
                    <Link className="link t-sm" to={`/ads/${x.adId}`}>Открыть копию</Link>
                  </div>
                  <div className="t-sm" style={{ marginTop: 4 }}>{x.text || <span className="c3">без текста</span>}</div>
                  <div className="t-xs c2" style={{ marginTop: 2 }}>{x.how}{x.lastSeenAt ? ` · последний раз видели ${fmtDT(x.lastSeenAt)}` : ''}</div>
                </div>
              ))}
          </div>
          <div className="card">
            <div className="card-head"><h3>Изменения содержимого</h3><span className="t-sm c2">{d.changes.length} за {d.days} дн.</span></div>
            {!d.changes.length ? <div className="t-sm c2">За {d.days} дн. собранные публикации не менялись: у каждой одна версия или правки не затронули текст, цену и файлы.</div>
              : d.changes.map((x) => (
                <div key={`${x.adId}-${x.toVersion}`} style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
                  <div className="row wrap-f" style={{ gap: 8 }}>
                    {who(x)}<span className="t-sm c2">версия {x.fromVersion} → {x.toVersion}{x.at ? ` · ${fmtDT(x.at)}` : ''}</span>
                    {x.kinds.map((k) => <span key={k} className={`tag ${k === 'terms_removed' ? 'red' : k === 'price' ? 'yellow' : 'gray'}`}>{KIND_LABEL[k]}</span>)}
                    <Link className="link t-sm" to={`/ads/${x.adId}`}>Разбор и diff</Link>
                  </div>
                  {x.kinds.includes('price') && <div className="t-sm" style={{ marginTop: 4 }}>Цена: {x.priceFrom || '—'} → {x.priceTo || '—'}</div>}
                  {(x.hunks ?? []).map((h, i) => (
                    <div key={i} className="t-sm" style={{ marginTop: 4, lineHeight: '20px' }}>
                      <span className="c2">{h.before} </span>
                      {h.removed && <del style={DEL}>{h.removed}</del>}{h.removed && h.added && ' '}
                      {h.added && <ins style={ADD}>{h.added}</ins>}
                      <span className="c2"> {h.after}</span>
                    </div>
                  ))}
                  {x.termsRemoved.length > 0 && <div className="t-xs c2" style={{ marginTop: 4 }}>Убраны условия: {x.termsRemoved.join(', ')}</div>}
                </div>
              ))}
          </div>
          <div className="t-xs c3">{d.limits}</div>
        </div>
      )}
    </>
  )
}
