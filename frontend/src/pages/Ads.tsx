import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button, ContentSwitcher, IconSwitch, Search } from '@carbon/react'
import { Add, Grid, List, Renew } from '@carbon/icons-react'
import FilterBar, { type Applied, type FilterDef } from '../components/FilterBar'
import { Highlight, PageHeader, PlatformIcon, Score, StatusIcon, StatusTag, fmtShort, platformMeta, statusMeta } from '../components/ui'
import type { AdStatus, Platform } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'

const ORDER: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']

/** Лента публикаций — список как в Linear: группы по статусу, плотные строки, фильтры-чипы */
export default function Ads() {
  const { ads, complexes, notify } = useStore()
  const nav = useNavigate()
  const [view, setView] = useState(0)
  const [q, setQ] = useState('')
  const [applied, setApplied] = useState<Applied[]>([])

  const defs: FilterDef[] = [
    { key: 'platform', label: 'Площадка', options: (Object.keys(platformMeta) as Platform[]).map((p) => ({ value: p, label: platformMeta[p].name })) },
    { key: 'status', label: 'Статус', options: ORDER.map((s) => ({ value: s, label: statusMeta[s].name })) },
    { key: 'zone', label: 'Оценка', options: [{ value: 'red', label: 'Красная' }, { value: 'yellow', label: 'Жёлтая' }, { value: 'green', label: 'Зелёная' }] },
    { key: 'source', label: 'Источник', options: [{ value: 'parser', label: 'Парсер' }, { value: 'manual', label: 'Вручную' }, { value: 'demo', label: 'Демо' }] },
  ]

  const rows = useMemo(() => ads.map((a) => { const c = complexes.find((x) => x.id === a.complexId); return { a, c, s: scoreAd(a, c) } })
    .filter(({ a, c, s }) => (!q || `${a.text} ${a.account} ${c?.name ?? ''}`.toLowerCase().includes(q.toLowerCase()))
      && applied.every((f) => f.values.includes(f.key === 'platform' ? a.platform : f.key === 'status' ? a.status : f.key === 'zone' ? s.zone : a.source)))
    .sort((x, y) => y.s.score - x.s.score), [ads, complexes, q, applied])

  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Мониторинг рекламы']]} title="Мониторинг рекламы"
        sub={<>Подсветка формулировок: <mark>продажа</mark> · <mark className="fund">сбор денег</mark> · <mark className="safe">признаки законности</mark></>}
        actions={<>
          <Button kind="tertiary" renderIcon={Renew} onClick={() => notify('Сбор публикаций запущен (демо)', 'info')}>Запустить сбор</Button>
          <Button renderIcon={Add} as={Link} to="/ads/new">Добавить вручную</Button>
        </>} />
      <FilterBar defs={defs} applied={applied} onChange={setApplied} right={<>
        <div style={{ width: 280 }}><Search size="sm" labelText="Поиск" placeholder="Текст, аккаунт, ЖК" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <ContentSwitcher size="sm" selectedIndex={view} onChange={({ index }) => setView(index ?? 0)} style={{ width: 'auto' }}>
          <IconSwitch name="list" text="Список"><List /></IconSwitch>
          <IconSwitch name="grid" text="Карточки"><Grid /></IconSwitch>
        </ContentSwitcher>
      </>} />

      {view === 0 ? ORDER.map((st) => {
        const g = rows.filter((x) => x.a.status === st)
        if (!g.length) return null
        return (
          <div key={st}>
            <div className="issue-group"><StatusIcon s={st} />{statusMeta[st].name}<span className="c">{g.length}</span></div>
            {g.map(({ a, c, s }) => (
              <Link key={a.id} to={`/ads/${a.id}`} className="issue">
                <PlatformIcon p={a.platform} />
                <span className="id">AD-{a.id.replace(/\D/g, '').padStart(3, '0') || a.id}</span>
                <span className="txt"><Highlight text={a.text} /></span>
                <span className="cx">{c ? `ЖК «${c.name}»` : '— ЖК не определён'}</span>
                <Score value={s.score} zone={s.zone} />
                <span className="date">{fmtShort(a.date)}</span>
                {a.assignee ? <span className="avatar" title={a.assignee}>ВН</span> : <span className="avatar empty" title="Не назначено" />}
              </Link>
            ))}
          </div>
        )
      }) : (
        <div className="page">
          <div className="cards">
            {rows.map(({ a, c, s }) => (
              <div key={a.id} className="pcard" onClick={() => nav(`/ads/${a.id}`)}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '10px 12px' }}><PlatformIcon p={a.platform} /><span className="label-01" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.account}</span></div>
                <div className="ph">
                  {a.image ? <img src={a.image} alt="" /> : <div style={{ height: '100%', display: 'grid', placeItems: 'center', background: 'var(--cds-layer-02)', padding: 16, textAlign: 'center' }} className="cell-title">{c ? `ЖК «${c.name}»` : 'Текстовое объявление'}</div>}
                  <Score value={s.score} zone={s.zone} />
                </div>
                <div className="b">
                  <div className="a" style={{ display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden', color: 'var(--cds-text-primary)' }}><Highlight text={a.text} /></div>
                  <div className="dev" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}><span>{c ? c.name : '—'} · {fmtShort(a.date)}</span><StatusTag s={a.status} /></div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}
