import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import Compare from '../components/Compare'
import MapView from '../components/MapView'
import { Gauge, Highlight, NeedsCheck, PageTitle, PlatformIcon, StatusTag, TriTag, ZoneTag, fmtDate, fmtDT } from '../components/ui'
import { developers } from '../data/demo'
import type { Stage, Tri } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCalendar, IcCamera, IcCheck, IcClose, IcCrane, IcFlag, IcHeart, IcHelp, IcMap, IcMore, IcShield, IcUsers, IcWorker } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
type Tab = 'basis' | 'risk' | 'ads' | 'photos' | 'dev'
const quarter = (d: string) => { const x = new Date(d); return `${['I', 'II', 'III', 'IV'][Math.floor(x.getMonth() / 3)]} кв. ${x.getFullYear()}` }

/** Карточка ЖК по образцу страницы новостройки на realty.yandex.ru */
export default function ComplexCard() {
  const { id } = useParams()
  const [sp] = useSearchParams()
  const { complexes, ads, notify } = useStore()
  const [tab, setTab] = useState<Tab>((sp.get('tab') as Tab) || 'basis')
  const [ph, setPh] = useState(0)
  const c = complexes.find((x) => x.id === id)
  if (!c) return <div className="empty">Объект не найден</div>
  const r = scoreComplex(c, ads)
  const dev = developers.find((d) => d.id === c.developerId)
  const linked = ads.filter((a) => a.complexId === c.id)
  const photos = [c.cover, ...c.photos.map((p) => p.src), '/img/site-b.jpg', '/img/city-3.jpg', '/img/city-panorama.jpg'].filter((v, i, a) => a.indexOf(v) === i).slice(0, 5)
  const stageIdx = STAGES.indexOf(c.stage)

  const basis = (title: string, v: Tri, doc?: string, note?: string) => (
    <div className={`promo-tile ${v === 'no' ? '' : ''}`}>
      <div className="row between">
        <span className={`zone-pic ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'gray'}`} style={{ width: 32, height: 32 }}>{v === 'yes' ? <IcCheck size={16} /> : v === 'no' ? <IcClose size={16} /> : <IcHelp size={16} />}</span>
        <TriTag v={v} />
      </div>
      <div className="h" style={{ marginTop: 6 }}>{title}</div>
      <div className="d">{doc ?? (v === 'unknown' ? 'Сведения не получены — запросить у уполномоченного органа' : 'Документ не найден')}</div>
      {note && <div className="d" style={{ marginTop: 'auto', color: 'var(--text-3)' }}>{note}</div>}
    </div>
  )

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Новостройки', '/complexes'], [`ЖК «${c.name}»`]]} title={<span className="row" style={{ gap: 14 }}>ЖК «{c.name}» <ZoneTag zone={r.zone} /></span>}
        sub={<>{c.address}, Усть-Каменогорск · <span className="row" style={{ display: 'inline-flex', gap: 4 }}><IcMap size={14} />{c.district}</span></>} />

      <div className="cols-r">
        <div>
          <div className="gallery">
            <div className="ph main-ph">
              <img src={photos[ph]} alt="" />
              <div className="tl"><span className="tag white">{c.stage === 'введён' ? 'сдан' : 'строится'}</span>{r.needsCheck && <span className="tag white">нужна проверка</span>}{c.idleDays >= 30 && <span className="tag white">стоит {c.idleDays} дн.</span>}</div>
            </div>
            <div className="thumbs">
              {photos.map((p, i) => (
                <button key={p} className={ph === i ? 'on' : ''} onClick={() => setPh(i)}>
                  <img src={p} alt="" />
                  {i === photos.length - 1 && c.photos.length > 1 && <span className="more" onClick={(e) => { e.stopPropagation(); setTab('photos') }}>Снимки · {c.photos.length}</span>}
                </button>
              ))}
            </div>
          </div>

          <h2 className="t-h2" style={{ margin: '40px 0 16px' }}>Ход строительства</h2>
          <div className="steps">
            {STAGES.map((s, i) => <div key={s} className={i < stageIdx ? 'done' : i === stageIdx ? 'cur' : ''} style={i === stageIdx ? ({ '--p': `${Math.max(15, c.progress)}%` } as React.CSSProperties) : undefined}>{s[0].toUpperCase() + s.slice(1)}</div>)}
          </div>

          <div className="subtabs" style={{ marginTop: 40 }}>
            {([['basis', 'Основания'], ['risk', 'Риск'], ['ads', 'Реклама', linked.length], ['photos', 'Снимки', c.photos.length], ['dev', 'Застройщик']] as const).map(([k, l, n]) => (
              <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}{n !== undefined && <span className="n">{n}</span>}</button>
            ))}
          </div>

          {tab === 'basis' && (
            <div className="fade">
              <h2 className="t-h2" style={{ marginBottom: 16 }}>Основание привлечения средств</h2>
              <div className="promo-tiles" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
                {basis('Гарантия ЕО (КЖК)', c.basis.guarantee, c.basis.guaranteeRef)}
                {basis('Разрешение МИО', c.basis.mio, c.basis.mioRef, c.basis.guarantee === 'yes' && c.basis.mio !== 'yes' ? 'При действующей гарантии ЕО отсутствие отдельного разрешения МИО — не R1' : undefined)}
                {basis('Банк второго уровня', c.basis.bvu, c.basis.bank)}
                {basis('Перечень ГАСК', c.inPublishedList, c.inPublishedList === 'yes' ? 'Instagram ГАСК ВКО, перечень от 1 октября 2026' : undefined, c.inPublishedList === 'no' ? 'Отсутствие в перечне само по себе не доказывает нарушение' : undefined)}
              </div>
              <h2 className="t-h2" style={{ margin: '40px 0 4px' }}>Параметры ЖК</h2>
              <div className="t-sm c2" style={{ marginBottom: 20 }}>ЖК «{c.name}»</div>
              <div className="params">
                <div className="param"><IcCalendar /><div><div className="l">Срок сдачи</div><div className="v">{quarter(c.plannedEnd)}</div></div></div>
                <div className="param"><IcCrane /><div><div className="l">Начало строительства</div><div className="v">{fmtDate(c.start)}</div></div></div>
                <div className="param"><IcBuilding /><div><div className="l">Этажность</div><div className="v">{c.floors} этажей · {c.flats} квартир</div></div></div>
                <div className="param"><IcWorker /><div><div className="l">Готовность</div><div className="v">{c.stage}, {c.progress}%</div></div></div>
              </div>
            </div>
          )}
          {tab === 'risk' && (
            <div className="fade">
              <h2 className="t-h2" style={{ marginBottom: 8 }}>Из чего сложился балл</h2>
              <p className="c2" style={{ marginBottom: 12 }}>{r.summary}</p>
              {r.factors.map((f) => (
                <div key={f.code} className={`factor ${f.state}`}><span className="code">{f.code}</span><div><div className="t">{f.title}</div><div className="e">{f.evidence}</div></div><span className="p">+{f.points}</span></div>
              ))}
              <Link to="/method" className="link t-sm" style={{ display: 'inline-block', marginTop: 12 }}>Как считается риск</Link>
            </div>
          )}
          {tab === 'ads' && (
            <div className="stack fade">
              {linked.length === 0 && <div className="empty">Публикаций по объекту не найдено</div>}
              {linked.map((a) => (
                <Link key={a.id} to={`/ads/${a.id}`} className="card" style={{ display: 'grid', gridTemplateColumns: a.image ? '120px 1fr' : '1fr', gap: 16, padding: 12 }}>
                  {a.image && <div className="ph" style={{ borderRadius: 12, aspectRatio: '1' }}><img src={a.image} alt="" /></div>}
                  <div style={{ padding: '4px 8px' }}>
                    <div className="row between"><div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={22} /><span className="t-sm c2">{a.account} · {fmtDT(a.date)}</span></div><StatusTag s={a.status} /></div>
                    <p style={{ marginTop: 10, lineHeight: '21px' }}><Highlight text={a.text} /></p>
                  </div>
                </Link>
              ))}
            </div>
          )}
          {tab === 'photos' && (
            <div className="fade">
              {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={c.photos[c.photos.length - 1]} /> : c.photos.length === 0 ? <div className="drop"><div><IcCamera /><p className="c2" style={{ marginTop: 8 }}>Снимков нет. Загрузите фото с дрона или камеры</p></div></div> : null}
              <div className="gcards" style={{ marginTop: 16, gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 16 }}>
                {c.photos.map((p, i) => (
                  <div key={i}>
                    <div className="ph" style={{ aspectRatio: '4/3', borderRadius: 12 }}><img src={p.src} alt="" /></div>
                    <div className="bold t-sm" style={{ marginTop: 8 }}>{fmtDate(p.date)}</div>
                    <div className="t-xs c2">{p.source} · активность: {p.activity}</div>
                    <div className="row wrap-f" style={{ gap: 4, marginTop: 6 }}>{p.fence && <TriTag v={p.fence} label="Забор" />}{p.bins && <TriTag v={p.bins} label="Контейнер" />}{p.wheelWash && <TriTag v={p.wheelWash} label="Мойка" />}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {tab === 'dev' && dev && (
            <div className="kv fade" style={{ maxWidth: 560 }}>
              {[['Наименование', dev.name], ['БИН', dev.bin], ['Руководитель', dev.director], ['Дата регистрации', fmtDate(dev.registered)], ['Объектов всего', dev.objects], ['Незавершённых', dev.unfinished], ['Сайт', dev.site ?? '—'], ['Instagram', dev.instagram ?? '—']].flatMap(([k, v]) => [<span key={`${k}k`}>{k}</span>, <span key={`${k}v`}>{v}</span>])}
            </div>
          )}
        </div>

        <aside className="stack sticky">
          <div style={{ padding: '4px 0' }}>
            <div className="row between"><span className="t-h3">Об объекте</span><div className="row" style={{ gap: 4 }}><button className="icon-btn" aria-label="Ещё"><IcMore /></button><button className="icon-btn" aria-label="В избранное"><IcHeart /></button></div></div>
            <div className="kv" style={{ marginTop: 14, gridTemplateColumns: '130px 1fr' }}>
              <span>Стадия</span><span>{c.stage}, {c.progress}%</span>
              <span>Квартир</span><span>{c.flats} · {c.floors} эт.</span>
              <span>Срок сдачи</span><span>{quarter(c.plannedEnd)}</span>
              <span>Реклама</span><span>{linked.length ? `${linked.length} публикации` : 'не найдена'}</span>
              <span>Риск-скоринг</span><span className="bold" style={{ color: `var(--${r.zone === 'yellow' ? 'amber' : r.zone})` }}>{r.score} из 100</span>
            </div>
            <div style={{ margin: '18px 0 20px' }}><Gauge value={r.score} /></div>
            {r.needsCheck && <div style={{ marginBottom: 12 }}><NeedsCheck /></div>}
            <div className="stack" style={{ gap: 8 }}>
              <button className="btn y full" onClick={() => notify('Кейс создан и добавлен в очередь')}><IcFlag />Создать кейс проверки</button>
              <button className="btn g full" onClick={() => notify('Запрос сведений сформирован')}>Запросить сведения</button>
            </div>
            <div className="row" style={{ marginTop: 18, alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <a className="link" onClick={() => setTab('dev')} style={{ cursor: 'pointer' }}>Застройщик {dev?.name}</a>
                <div className="t-xs c3" style={{ marginTop: 2 }}>{(dev?.objects ?? 0) - (dev?.unfinished ?? 0)} сдано · {dev?.unfinished} строится</div>
              </div>
              <span className="tag dark" style={{ height: 28 }}><IcUsers size={12} />{dev?.name.replace(/ТОО «|»/g, '').split(' ')[0]}</span>
            </div>
            <p className="t-xs c2 row" style={{ marginTop: 16, gap: 6, alignItems: 'flex-start' }}><IcShield size={14} />ИИ даёт рекомендацию. Решение о нарушении принимает аналитик.</p>
          </div>
          <div className="ph" style={{ height: 180, borderRadius: 16 }}>
            <MapView items={[{ c, zone: r.zone, score: r.score }]} center={[c.lng, c.lat]} zoom={14.5} interactive={false} labels />
          </div>
        </aside>
      </div>
    </>
  )
}
