// «Кто продаёт»: группы аккаунтов, связанных общим телефоном, WhatsApp, БИН, именем аккаунта или картинкой
// (GET /api/sellers), схема связей «аккаунты ↔ телефоны ↔ ЖК» и доказательства — дословные фрагменты публикаций;
// вкладка «Цена» — цена за м² против медианы города и отметка «100% оплата» (GET /api/price-anomalies).
// Всё — гипотезы для проверки вне балла риска; офлайн-копии нет — без сервера экран объясняет это.
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Cap, Board, Kpi, Row } from '../components/StatBoard'
import { PageTitle, PlatformIcon, Seg, adKey, fmtShort, plural } from '../components/ui'
import type { Platform } from '../data/types'
import { errorText, mediaSrc } from '../lib/api'
import { useT } from '../lib/i18n'
import { from } from '../lib/nav'
import { PLATFORMS } from '../lib/platform'
import {
  HYPOTHESIS, METHOD_LABEL, ROOMS_LABEL, SOURCE_LABEL, belowText, fmtPerM2, getPrices, getSeller, listSellers, nodeLabel, radialLayout,
  type PriceReport, type SellerDetail, type SellerGroup, type SellersList,
} from '../lib/sellers'
import { useStore } from '../lib/store'
import '../styles/sellers.css'

type Tab = 'groups' | 'price'
const platformOf = (p?: string | null): Platform | null => (p && Object.hasOwn(PLATFORMS, p) ? p as Platform : null)
const day = (d?: string | null) => (d ? fmtShort(d) : '—')
const NODE: Record<string, { fill: string; r: number; label: string }> = {
  account: { fill: '#0065d9', r: 13, label: 'аккаунт' },
  phone: { fill: '#f59d00', r: 10, label: 'телефон' },
  bin: { fill: '#7a5af8', r: 10, label: 'БИН' },
  complex: { fill: '#00b341', r: 11, label: 'ЖК' },
}
const EDGE_DASH: Record<string, string | undefined> = { handle: '4 4', mention: '2 4', image: '8 4' }

export default function Sellers() {
  const { mode } = useStore()
  const { t } = useT()
  const [sp, setSp] = useSearchParams()
  const tab: Tab = sp.get('tab') === 'price' ? 'price' : 'groups'
  const key = sp.get('key')
  const set = (k: string, v: string | null) => setSp((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n }, { replace: true })
  const [list, setList] = useState<{ data?: SellersList | null; err?: string } | null>(null)
  const [price, setPrice] = useState<{ data?: PriceReport | null; err?: string } | null>(null)
  const [allPrices, setAllPrices] = useState(false)
  const [onlyLinked, setOnlyLinked] = useState(true)

  useEffect(() => {
    if (mode === 'loading') return
    let on = true
    listSellers().then((data) => on && setList({ data }), (e) => on && setList({ err: errorText(e) }))
    return () => { on = false }
  }, [mode])
  useEffect(() => {
    if (mode === 'loading') return
    let on = true
    getPrices(allPrices).then((data) => on && setPrice({ data }), (e) => on && setPrice({ err: errorText(e) }))
    return () => { on = false }
  }, [mode, allPrices])

  const groups = useMemo(() => (list?.data?.groups ?? []).filter((g) => !onlyLinked || g.linked || g.key === key), [list, onlyLinked, key])
  const cur = key ?? groups[0]?.key ?? null
  const st = list?.data?.stats
  const unavailable = list && !list.err && list.data === null

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], [t('sellers.title')]]} title={t('sellers.title')}
        sub={<span className="t-sm c2">{t('sellers.sub')} · {HYPOTHESIS}, в балл риска не входит</span>} />
      <Board kind="sellers">
        <Kpi big em="devs" value={st?.groups ?? '—'} label="продавцов (групп аккаунтов)" onClick={() => { set('tab', null); setOnlyLinked(false) }} />
        <div className="sb-col">
          <Cap aside="гипотеза для проверки">Связи</Cap>
          <Row em="search" tone="yellow" label="Связанных групп (2+ аккаунта)" value={st?.linked ?? '—'} onClick={() => { set('tab', null); setOnlyLinked(true) }} active={tab === 'groups' && onlyLinked} />
          <Row em="feed" tone="blue" label="Телефонов в рекламе / общих" value={st ? `${st.phones} / ${st.sharedPhones}` : '—'} />
          <Row em="photos" label="Пар аккаунтов с одной картинкой" value={st?.imageLinks ?? '—'} />
        </div>
        <div className="sb-sep" />
        <div className="sb-col">
          <Cap aside="контекст, не балл">Цена</Cap>
          <Row em="docs" tone="red" label="Ниже медианы на 30% и больше" value={price?.data?.anomalies ?? '—'} onClick={() => set('tab', 'price')} active={tab === 'price'} />
          <Row em="calendar" label="Медиана города, ₸/м²" value={price?.data?.median ? fmtPerM2(price.data.median).replace(' ₸/м²', '') : '—'} onClick={() => set('tab', 'price')} />
          <Row em="flag" tone="yellow" label="Отметка «100% оплата»" value={price?.data?.fullPayment.length ?? '—'} onClick={() => set('tab', 'price')} />
        </div>
      </Board>

      <div className="row between" style={{ margin: '20px 0 16px', gap: 12 }}>
        <Seg value={tab} onChange={(v) => set('tab', v === 'groups' ? null : v)} items={[['groups', t('sellers.tabGroups')], ['price', t('sellers.tabPrice')]]} />
        {tab === 'groups'
          ? <label className="row t-sm" style={{ gap: 8 }}><input type="checkbox" checked={onlyLinked} onChange={(e) => setOnlyLinked(e.target.checked)} />только связанные (2+ аккаунта)</label>
          : <label className="row t-sm" style={{ gap: 8 }}><input type="checkbox" checked={allPrices} onChange={(e) => setAllPrices(e.target.checked)} />все публикации с ценой за м²</label>}
      </div>

      {mode === 'snapshot' || unavailable ? <div className="box warn t-sm">«Кто продаёт» считается на сервере: в офлайн-копии и на сервере старой версии этого раздела нет.</div>
        : tab === 'groups' ? (
          list?.err ? <div className="box warn t-sm">Список продавцов не загрузился: {list.err}</div>
            : !list ? <div className="empty">Загружаем…</div>
            : <div className="sl-grid">
              <div className="sl-list">
                <div className="t-sm c2">{groups.length} {plural(groups.length, ['группа', 'группы', 'групп'])}: по числу аккаунтов, затем публикаций</div>
                {groups.map((g) => <GroupItem key={g.key} g={g} on={g.key === cur} onClick={() => set('key', g.key)} />)}
                {!groups.length && <div className="empty">Связанных аккаунтов не нашли — снимите «только связанные», чтобы увидеть всех продавцов</div>}
              </div>
              {cur && list.data && <GroupDetail key={cur} k={cur} list={list.data} />}
            </div>
        ) : <PriceTab p={price} />}
    </>
  )
}

function GroupItem({ g, on, onClick }: { g: SellerGroup; on: boolean; onClick: () => void }) {
  const kinds = [...new Set(g.links.map((l) => l.label))]
  return (
    <button className={`sl-item ${on ? 'on' : ''}`} onClick={onClick} aria-pressed={on}>
      <span className="t">{g.title}</span>
      <span className="sl-meta t-sm c2">
        {g.accounts.length} {plural(g.accounts.length, ['аккаунт', 'аккаунта', 'аккаунтов'])} · {g.adsCount} публ.
        {g.phones.length > 0 && ` · ${g.phones.length} тел.`}{g.complexes.length > 0 && ` · ЖК: ${g.complexes.map((c) => c.name).join(', ')}`}
      </span>
      {kinds.length > 0 && <span className="sl-meta">{kinds.map((k) => <span key={k} className="tag yellow">{k}</span>)}</span>}
    </button>
  )
}

function GroupDetail({ k, list }: { k: string; list: SellersList }) {
  const [d, setD] = useState<{ data?: SellerDetail; err?: string } | null>(null)
  useEffect(() => {
    let on = true
    getSeller(k).then((data) => on && setD({ data }), (e) => on && setD({ err: errorText(e) }))
    return () => { on = false }
  }, [k])
  if (!d) return <div className="card"><div className="t-sm c2">Загружаем…</div></div>
  if (d.err || !d.data) return <div className="card"><div className="box warn t-sm">Продавец не загрузился: {d.err}</div></div>
  const g = d.data
  const back = from('Кто продаёт', `/sellers?key=${encodeURIComponent(g.key)}`)
  const images = g.links.filter((l) => l.kind === 'image')
  const accName = (key: string) => g.accounts.find((a) => a.key === key)?.account ?? key
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="card">
        <div className="card-head"><h3>{g.title}</h3><span className="tag yellow" title={g.note}>гипотеза для проверки</span></div>
        <p className="t-sm c2" style={{ marginBottom: 12 }}>
          {g.accounts.length} {plural(g.accounts.length, ['аккаунт', 'аккаунта', 'аккаунтов'])} · {g.adsCount} {plural(g.adsCount, ['публикация', 'публикации', 'публикаций'])} · {day(g.firstSeen)} — {day(g.lastSeen)}
        </p>
        <Graph g={g} />
        <p className="t-xs c2" style={{ marginTop: 10, lineHeight: '17px' }}>{g.note}</p>
      </div>

      <div className="card">
        <div className="card-head"><h3>Почему вместе</h3><span className="t-sm c2">{g.links.length ? `${g.links.length} ${plural(g.links.length, ['связь', 'связи', 'связей'])} аккаунтов` : 'один аккаунт'}</span></div>
        {!g.links.length && <p className="t-sm c2">Общих контактов и картинок с другими аккаунтами не нашли. Ниже — контакты этого продавца.</p>}
        {images.length > 0 && (
          <div style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
            <div className="row wrap-f" style={{ gap: 8 }}><span className="tag blue">одна картинка</span><b>одна и та же картинка у {images.length} {plural(images.length, ['пары', 'пар', 'пар'])} аккаунтов</b></div>
            <p className="t-sm c2" style={{ margin: '4px 0 8px' }}>Перцептивный хэш (dHash, 64 бита): расстояние до {list.imageMaxDistance} бит — та же картинка после пережатия и смены размера. Бывает у агента и застройщика с общим рендером.</p>
            <div className="sl-pairs">
              {images.flatMap((l) => l.evidence.slice(0, 1).map((e) => (
                <div key={`${e.adId}-${e.otherAdId}`} className="sl-pair">
                  {[[e.mediaId, e.adId], [e.otherMediaId, e.otherAdId]].map(([m, ad]) => (
                    <Link key={String(ad)} to={`/ads/${ad}`} state={back} title={`Открыть ${adKey(String(ad))}`}>
                      {m ? <img src={mediaSrc(`/api/media/${m}`)} alt="" loading="lazy" /> : <span className="noimg" />}<span className="t-xs">{adKey(String(ad))}</span>
                    </Link>
                  ))}
                  <span className="t-xs c2">расстояние {e.distance} из 64</span>
                </div>
              )))}
            </div>
          </div>
        )}
        {g.links.filter((l) => l.kind !== 'image').map((l) => (
          <div key={`${l.kind}-${l.value}`} style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
            <div className="row wrap-f" style={{ gap: 8 }}><span className="tag blue">{l.label}</span><b>{l.kind === 'image' ? 'одна и та же картинка' : l.shown}</b>
              <span className="t-sm c2">· {l.accounts.map(accName).join(' ↔ ')}</span></div>
            {l.evidence.slice(0, 4).map((e, i) => <EvidenceLine key={i} e={e} back={back} />)}
            {l.kind === 'handle' && <div className="t-sm c2" style={{ marginTop: 4 }}>Одно имя аккаунта на разных площадках — вероятно, один владелец; проверьте ссылки в профилях.</div>}
          </div>
        ))}
        {g.phones.filter((p) => p.accounts.length === 1).map((p) => (
          <div key={p.digits} style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
            <div className="row wrap-f" style={{ gap: 8 }}><span className="tag gray">{p.whatsapp ? 'WhatsApp' : 'телефон'}</span><b>{p.phone}</b><span className="t-sm c2">· {p.ads} публ.</span></div>
            {p.evidence.slice(0, 2).map((e, i) => <EvidenceLine key={i} e={e} back={back} />)}
          </div>
        ))}
        {g.bins.map((b) => (
          <div key={b.bin} style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
            <div className="row wrap-f" style={{ gap: 8 }}><span className="tag gray">БИН</span><b>{b.bin}</b>
              {b.developerId && <Link className="t-sm" to={`/developers?dev=${b.developerId}`}>{b.developerName}</Link>}</div>
            {b.evidence.slice(0, 2).map((e, i) => <EvidenceLine key={i} e={e} back={back} />)}
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head"><h3>Аккаунты и ЖК</h3></div>
        {g.accounts.map((a) => {
          const p = platformOf(a.platform)
          return (
            <div key={a.key} className="row between" style={{ padding: '8px 0', gap: 12 }}>
              <span className="row" style={{ gap: 8, minWidth: 0 }}>{p && <PlatformIcon p={p} size={22} />}<span>{a.account}</span></span>
              <span className="t-sm c2">{a.ads} публ. · {day(a.firstSeen)} — {day(a.lastSeen)}</span>
            </div>
          )
        })}
        {g.complexes.length > 0 && <div className="row wrap-f" style={{ gap: 6, paddingTop: 10, borderTop: '1px solid var(--line)' }}>
          {g.complexes.map((c) => <Link key={c.id} to={`/complexes/${c.id}`} state={back} className={`tag ${c.confirmed ? 'green' : 'gray'}`}
            title={c.confirmed ? 'Привязка публикаций к ЖК подтверждена аналитиком' : 'Привязка — гипотеза ИИ'}>ЖК «{c.name}» · {c.ads}</Link>)}
        </div>}
      </div>

      <div className="card">
        <div className="card-head"><h3>Публикации</h3><span className="t-sm c2">{g.ads.length}</span></div>
        {g.ads.slice(0, 30).map((a) => (
          <Link key={a.id} to={`/ads/${a.id}`} state={back} className="row" style={{ gap: 10, padding: '8px 0', borderTop: '1px solid var(--line)', color: 'inherit', alignItems: 'flex-start' }}>
            <span className="t-sm bold" style={{ whiteSpace: 'nowrap' }}>{adKey(a.id)}</span>
            <span className="t-sm" style={{ flex: 1, minWidth: 0 }}>{a.snippet || '—'}<span className="c2"> · {a.account} · {day(a.date)}{a.complexName ? ` · ЖК «${a.complexName}»` : ''}</span></span>
          </Link>
        ))}
        {g.ads.length > 30 && <p className="t-sm c2" style={{ paddingTop: 8 }}>и ещё {g.ads.length - 30}</p>}
      </div>
    </div>
  )
}

function EvidenceLine({ e, back }: { e: { adId: string; source: string; quote: string; otherAdId?: string }; back: ReturnType<typeof from> }) {
  return (
    <div className="sl-ev">
      «{e.quote}» <span className="src">— {SOURCE_LABEL[e.source] ?? e.source}, <Link to={`/ads/${e.adId}`} state={back}>{adKey(e.adId)}</Link>
        {e.otherAdId && <> и <Link to={`/ads/${e.otherAdId}`} state={back}>{adKey(e.otherAdId)}</Link></>}</span>
    </div>
  )
}

function Graph({ g }: { g: SellerDetail }) {
  const W = 720, H = 380
  const pos = useMemo(() => radialLayout(g.graph.nodes, g.graph.edges, W, H), [g])
  const at = new Map(pos.map((p) => [p.id, p]))
  return (
    <>
      <svg className="sl-graph" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Схема связей: ${g.graph.nodes.length} узлов`}>
        {g.graph.edges.map((e, i) => {
          const a = at.get(e.a), b = at.get(e.b)
          return a && b ? <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={e.kind === 'complex' ? '#00b341' : e.kind === 'image' ? '#7a5af8' : '#9aa0a6'}
            strokeWidth={1.6} strokeDasharray={EDGE_DASH[e.kind]} opacity={0.8} /> : null
        })}
        {pos.map((n) => (
          <g key={n.id}>
            <circle cx={n.x} cy={n.y} r={NODE[n.kind].r} fill={NODE[n.kind].fill}><title>{`${NODE[n.kind].label}: ${n.label}`}</title></circle>
            <text x={n.x} y={n.y + NODE[n.kind].r + 14} textAnchor="middle">{nodeLabel(n.label)}</text>
          </g>
        ))}
      </svg>
      <div className="sl-legend">
        {Object.entries(NODE).map(([k, v]) => <span key={k}><i style={{ background: v.fill }} />{v.label}</span>)}
        <span>пунктир — одно имя / упоминание / одна картинка</span>
      </div>
    </>
  )
}

function PriceTab({ p }: { p: { data?: PriceReport | null; err?: string } | null }) {
  if (!p) return <div className="empty">Загружаем…</div>
  if (p.err || !p.data) return <div className="box warn t-sm">Цены не загрузились{p.err ? `: ${p.err}` : ''}</div>
  const r = p.data
  const back = from('Кто продаёт · цена', '/sellers?tab=price')
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="card">
        <div className="card-head"><h3>Медиана цены за м²</h3><span className="t-sm c2">{r.n} {plural(r.n, ['публикация', 'публикации', 'публикаций'])} с ценой</span></div>
        <div className="row wrap-f" style={{ gap: 24 }}>
          <div><div className="t-xs c2">Город</div><div className="t-h3">{fmtPerM2(r.median)}</div></div>
          {Object.entries(r.byRooms).map(([k, v]) => (
            <div key={k} title={v.used ? 'Для этих публикаций сравнение — с медианой по числу комнат' : `Меньше ${r.minGroup} публикаций — сравнение с медианой города`}>
              <div className="t-xs c2">{ROOMS_LABEL(k)} · {v.n}</div><div className="bold" style={{ color: v.used ? undefined : 'var(--text-3)' }}>{fmtPerM2(v.median)}</div>
            </div>
          ))}
        </div>
        <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>Ниже рынка — дешевле {Math.round(r.threshold * 100)}% медианы (по числу комнат, если таких публикаций {r.minGroup} и больше). {r.note}</p>
      </div>
      <div className="card">
        <div className="card-head"><h3>{r.items.some((x) => !x.anomaly) && r.anomalies === 0 && r.items.length > 5 ? 'Публикации с ценой за м²' : 'Ниже рынка и «100% оплата»'}</h3>
          <span className="t-sm c2">ниже рынка: {r.anomalies}</span></div>
        {!r.items.length ? <p className="t-sm c2">Публикаций с ценой ниже {Math.round(r.threshold * 100)}% медианы нет. Отметить «все публикации с ценой за м²» — чтобы увидеть, из чего посчитана медиана.</p> : (
          <div style={{ overflowX: 'auto' }}>
            <table className="sl-tbl">
              <thead><tr><th>Публикация</th><th>Цена за м²</th><th>Медиана</th><th>Отклонение</th><th>Откуда цена</th></tr></thead>
              <tbody>
                {r.items.map((x) => (
                  <tr key={x.adId} className={x.anomaly ? 'anom' : ''}>
                    <td><Link to={`/ads/${x.adId}`} state={back}>{adKey(x.adId)}</Link><div className="t-xs c2">{x.account}{x.complexName ? ` · ЖК «${x.complexName}»` : ''} · {ROOMS_LABEL(x.rooms)}</div></td>
                    <td className="num">{x.from ? 'от ' : ''}{fmtPerM2(x.perM2)}</td>
                    <td className="num">{fmtPerM2(x.median)}<div className="t-xs c2">{x.medianBasis === 'rooms' ? 'по комнатам' : 'город'}</div></td>
                    <td>{x.anomaly ? <span className="tag red">{belowText(x.belowPct)}</span> : <span className="t-sm c2">{belowText(x.belowPct)}</span>}
                      {x.fullPayment && <div style={{ marginTop: 4 }}><span className="tag yellow" title={`«${x.fullPayment.quote}»`}>100% оплата</span></div>}</td>
                    <td className="t-sm">«{x.quote}»<div className="t-xs c2">{x.method ? METHOD_LABEL[x.method] : ''} · {SOURCE_LABEL[x.source ?? ''] ?? x.source}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {r.fullPayment.length > 0 && (
        <div className="card">
          <div className="card-head"><h3>Отметка «100% оплата»</h3><span className="t-sm c2">{r.fullPayment.length}</span></div>
          <p className="t-sm c2" style={{ marginBottom: 8 }}>Скидка за полную оплату на стадии строительства — повод проверить основание продажи, но не нарушение сама по себе.</p>
          {r.fullPayment.map((x) => (
            <div key={x.adId} className="sl-ev">«{x.fullPayment.quote}» <span className="src">— <Link to={`/ads/${x.adId}`} state={back}>{adKey(x.adId)}</Link> · {x.account}{x.perM2 ? ` · ${fmtPerM2(x.perM2)}` : ''}</span></div>
          ))}
        </div>
      )}
    </div>
  )
}
