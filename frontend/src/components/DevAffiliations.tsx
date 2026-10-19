// Профиль застройщика: «Проверка по БИН и связи» (GET /api/developers/{id}/affiliations) — БИН и контрольная цифра,
// руководитель, первое появление, ЖК и публикации, уполномоченные компании из открытого реестра, совпадения с другими
// застройщиками (руководитель, телефон, уполномоченная компания, общий продавец) и ссылки на официальные сервисы поиска
// по БИН — система их не опрашивает. Совпадение — гипотеза для проверки; без сервера блока нет.
import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { fmtDate } from './ui'
import { useT } from '../lib/i18n'
import { getAffiliations, type Affiliations } from '../lib/sellers'
import { useStore } from '../lib/store'
import '../styles/sellers.css'

export function DevAffiliations({ id }: { id: string }) {
  const { mode } = useStore()
  const { t } = useT()
  const [a, setA] = useState<{ data?: Affiliations | null; err?: string } | null>(null)
  useEffect(() => {
    if (mode !== 'api') return
    let on = true
    getAffiliations(id).then((data) => on && setA({ data }), (e) => on && setA({ err: e instanceof Error ? e.message : String(e) }))
    return () => { on = false }
  }, [id, mode])
  if (mode !== 'api' || !a || a.data === null) return null
  if (a.err || !a.data) return <div className="box warn t-sm" style={{ margin: '0 24px 12px' }}>Связи застройщика не загрузились: {a.err}</div>
  const x = a.data
  const d = x.developer
  const line = (k: string, v: ReactNode) => (
    <div className="row between" style={{ gap: 12, padding: '6px 0', borderTop: '1px solid var(--line)' }}><span className="t-sm c2">{k}</span><span className="t-sm" style={{ textAlign: 'right' }}>{v}</span></div>
  )
  return (
    <div style={{ padding: '4px 24px 16px' }}>
      <div className="row between" style={{ margin: '12px 0 8px' }}><h3 className="t-h3" style={{ fontSize: 18 }}>{t('sellers.devTitle')}</h3><span className="tag gray">по нашим данным</span></div>
      {line('БИН', d.bin ? <>{d.bin} {d.binValid ? <span className="tag green">контрольная цифра верна</span> : <span className="tag yellow">контрольная цифра не сходится</span>}</> : 'нет в данных')}
      {line('Руководитель', d.director || 'нет в данных — см. КГД или stat.gov.kz')}
      {line('Первое появление', x.firstSeen ? `${fmtDate(x.firstSeen)}${x.firstAd ? ` · первая публикация ${fmtDate(x.firstAd)}` : ''}` : 'нет в данных')}
      {line('ЖК и публикации', `${x.complexes.length} ЖК · ${x.adsCount} публ. (привязка подтверждена у ${x.confirmedAds})`)}
      {x.phones.length > 0 && line('Телефоны в рекламе', x.phones.map((p) => p.phone).join(', '))}
      {x.accounts.length > 0 && line('Аккаунты', x.accounts.slice(0, 5).map((ac) => `${ac.account} (${ac.ads})`).join(', '))}
      {x.agents.map((ag) => <Fragment key={ag.bin ?? ag.name}>{line('Уполномоченная компания', <>{ag.name}{ag.bin && ` · БИН ${ag.bin}`}<div className="t-xs c2">{ag.sources.join(', ')} · {ag.records} зап.</div></>)}</Fragment>)}
      <div style={{ borderTop: '1px solid var(--line)', padding: '8px 0' }}>
        <div className="t-sm bold" style={{ marginBottom: 4 }}>Совпадения с другими застройщиками</div>
        {x.related.length ? x.related.map((r) => (
          <div key={r.id} className="t-sm" style={{ padding: '4px 0' }}>
            <Link to={`/developers?dev=${r.id}`}>{r.name}</Link>{r.bin && <span className="c2"> · БИН {r.bin}</span>}
            <div className="row wrap-f" style={{ gap: 6, marginTop: 4 }}>{r.reasons.map((z, i) => <span key={i} className="tag yellow" title={z.value}>{z.label}: {z.value}</span>)}</div>
          </div>
        )) : <div className="t-sm c2">Общих руководителя, телефона и уполномоченной компании в наших данных нет.</div>}
        {x.sellerGroups.filter((g) => g.accounts > 1).map((g) => (
          <div key={g.key} className="t-sm" style={{ paddingTop: 4 }}>Продавец в рекламе: <Link to={`/sellers?key=${encodeURIComponent(g.key)}`}>{g.title}</Link><span className="c2"> · {g.accounts} акк., {g.ads} публ.</span></div>
        ))}
        <p className="t-xs c2" style={{ marginTop: 6, lineHeight: '17px' }}>{x.note}</p>
      </div>
      <div style={{ borderTop: '1px solid var(--line)', paddingTop: 8 }}>
        <div className="t-sm bold" style={{ marginBottom: 4 }}>Проверить в официальных сервисах{d.bin ? ` (БИН ${d.bin})` : ''}</div>
        {x.lookups.map((l) => (
          <div key={l.url} className="t-sm" style={{ padding: '3px 0' }}><a href={l.url} target="_blank" rel="noopener noreferrer">{l.label} ↗</a><span className="c2"> — {l.note}</span></div>
        ))}
        <p className="t-xs c2" style={{ marginTop: 4 }}>Ссылки открывает аналитик; система эти сервисы не опрашивает.</p>
      </div>
    </div>
  )
}
