import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import Compare from '../components/Compare'
import MapView from '../components/MapView'
import { Cell, Gauge, Highlight, NeedsCheck, PageTitle, PlatformIcon, Seg, StatusTag, TriTag, ZoneTag, fmtDate, fmtDT } from '../components/ui'
import { developers } from '../data/demo'
import type { Stage, Tri } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCamera, IcCheck, IcClose, IcDoc, IcFlag, IcHelp, IcInfo } from '../ui/icons'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']
type Tab = 'basis' | 'ads' | 'photos' | 'dev'

/** Карточка объекта: галерея и факты как на страницах ЖК, справа «чек» риска с жёлтой кнопкой */
export default function ComplexCard() {
  const { id } = useParams()
  const [sp] = useSearchParams()
  const { complexes, ads, notify } = useStore()
  const [tab, setTab] = useState<Tab>((sp.get('tab') as Tab) || 'basis')
  const c = complexes.find((x) => x.id === id)
  if (!c) return <div className="empty">Объект не найден</div>
  const r = scoreComplex(c, ads)
  const dev = developers.find((d) => d.id === c.developerId)
  const linked = ads.filter((a) => a.complexId === c.id)
  const photos = [c.cover, ...c.photos.map((p) => p.src), '/img/site-b.jpg', '/img/city-panorama.jpg'].filter((v, i, a) => a.indexOf(v) === i)
  const stageIdx = STAGES.indexOf(c.stage)

  const basis = (title: string, v: Tri, doc?: string, note?: string) => (
    <Cell inset title={title} sub={<>{doc ?? (v === 'unknown' ? 'Сведения не получены — запросить у уполномоченного органа' : 'Документ не найден')}{note && <><br /><span className="c3">{note}</span></>}</>}
      icon={<span className={`zone-pic ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'all'}`} style={{ width: 40, height: 40 }}>{v === 'yes' ? <IcCheck size={20} strokeWidth={2.5} /> : v === 'no' ? <IcClose size={20} strokeWidth={2.5} /> : <IcHelp size={20} />}</span>}
      end={<TriTag v={v} />} />
  )

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Реестр ЖК', '/complexes'], [c.name]]} title={`ЖК «${c.name}»`}
        sub={<>{c.address}, {c.district} · {dev?.name} · БИН <span className="num">{dev?.bin}</span></>} />

      <div className="cols-r">
        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="gallery">
              {photos.slice(0, 3).map((p, i) => (
                <div key={i} className="ph">
                  <img src={p} alt="" />
                  {i === 0 && <div className="tl"><ZoneTag zone={r.zone} /><span className="tag dark">{c.stage}</span>{r.needsCheck && <NeedsCheck />}</div>}
                  {i === 2 && c.photos.length > 1 && <button className="more" onClick={() => setTab('photos')}>Все снимки · {c.photos.length}</button>}
                </div>
              ))}
            </div>
            <div className="tiles" style={{ gridTemplateColumns: 'repeat(4, 1fr)', padding: '4px 8px 8px' }}>
              {[['Этажность', `${c.floors} этажей`], ['Квартир', String(c.flats)], ['Начало', fmtDate(c.start)], ['Плановая сдача', fmtDate(c.plannedEnd)]].map(([k, v]) => (
                <div key={k} className="tile" style={{ minHeight: 0, background: 'var(--bg)' }}><span className="nm c2" style={{ fontSize: 13 }}>{k}</span><span className="vl">{v}</span></div>
              ))}
            </div>
            <div style={{ padding: '12px 24px 24px' }}>
              <div className="row between" style={{ marginBottom: 12 }}><span className="bold">Ход строительства</span><span className="t-sm c2 num">готовность {c.progress}%</span></div>
              <div className="steps">
                {STAGES.map((s, i) => <div key={s} className={i < stageIdx ? 'done' : i === stageIdx ? 'cur' : ''} style={i === stageIdx ? ({ '--p': `${Math.max(15, c.progress)}%` } as React.CSSProperties) : undefined}>{s[0].toUpperCase() + s.slice(1)}</div>)}
              </div>
            </div>
          </div>

          <Seg value={tab} onChange={setTab} items={[['basis', 'Основания'], ['ads', `Реклама · ${linked.length}`], ['photos', `Снимки · ${c.photos.length}`], ['dev', 'Застройщик']]} />

          {tab === 'basis' && (
            <div className="card fade">
              <div className="card-head"><h3>Основание привлечения средств</h3></div>
              {basis('Гарантия ЕО (КЖК)', c.basis.guarantee, c.basis.guaranteeRef)}
              {basis('Разрешение МИО', c.basis.mio, c.basis.mioRef, c.basis.guarantee === 'yes' && c.basis.mio !== 'yes' ? 'При действующей гарантии ЕО отсутствие отдельного разрешения МИО — не R1' : undefined)}
              {basis('Банк второго уровня', c.basis.bvu, c.basis.bank)}
              {basis('Перечень ГАСК', c.inPublishedList, c.inPublishedList === 'yes' ? 'Instagram ГАСК ВКО, перечень от 1 октября 2026' : undefined, c.inPublishedList === 'no' ? 'Отсутствие в перечне само по себе не доказывает нарушение' : undefined)}
              <div style={{ height: 8 }} />
            </div>
          )}
          {tab === 'ads' && (
            <div className="stack fade">
              {linked.length === 0 && <div className="card empty">Публикаций по объекту не найдено</div>}
              {linked.map((a) => (
                <Link key={a.id} to={`/ads/${a.id}`} className="card" style={{ display: 'grid', gridTemplateColumns: a.image ? '120px 1fr' : '1fr', gap: 18, padding: 8 }}>
                  {a.image && <div className="ph" style={{ borderRadius: 18, aspectRatio: '1' }}><img src={a.image} alt="" /></div>}
                  <div style={{ padding: '10px 16px 10px 8px' }}>
                    <div className="row between"><div className="row" style={{ gap: 8 }}><PlatformIcon p={a.platform} size={24} /><span className="t-sm c2">{a.account} · {fmtDT(a.date)}</span></div><StatusTag s={a.status} /></div>
                    <p style={{ marginTop: 10, lineHeight: '22px' }}><Highlight text={a.text} /></p>
                  </div>
                </Link>
              ))}
            </div>
          )}
          {tab === 'photos' && (
            <div className="card card-pad fade">
              {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={c.photos[c.photos.length - 1]} /> : c.photos.length === 0 ? <div className="drop"><div><IcCamera /><p className="c2" style={{ marginTop: 8 }}>Снимков нет. Загрузите фото с дрона или камеры</p></div></div> : null}
              <div className="gcards" style={{ marginTop: 16, gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 12 }}>
                {c.photos.map((p, i) => (
                  <div key={i} style={{ background: 'var(--bg)', borderRadius: 20, overflow: 'hidden' }}>
                    <div className="ph" style={{ aspectRatio: '4/3', borderRadius: 16, margin: 4 }}><img src={p.src} alt="" /></div>
                    <div style={{ padding: '8px 12px 12px' }}>
                      <div className="bold t-sm">{fmtDate(p.date)}</div>
                      <div className="t-xs c2" style={{ marginTop: 2 }}>{p.source} · активность: {p.activity}</div>
                      <div className="row wrap-f" style={{ gap: 4, marginTop: 8 }}>{p.fence && <TriTag v={p.fence} label="Забор" />}{p.bins && <TriTag v={p.bins} label="Контейнер" />}{p.wheelWash && <TriTag v={p.wheelWash} label="Мойка" />}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {tab === 'dev' && dev && (
            <div className="card fade">
              {[['Наименование', dev.name], ['БИН', dev.bin], ['Руководитель', dev.director], ['Дата регистрации', fmtDate(dev.registered)], ['Объектов всего', dev.objects], ['Незавершённых', dev.unfinished], ['Сайт', dev.site ?? '—'], ['Instagram', dev.instagram ?? '—']].map(([k, v]) => (
                <Cell key={String(k)} title={<span className="c2 t-sm">{k}</span>} end={<span style={{ color: 'var(--text)' }}>{v}</span>} />
              ))}
            </div>
          )}
        </div>

        <aside className="stack sticky">
          <div className="card card-pad">
            <div className="row between"><span className="bold">Риск-скоринг</span><ZoneTag zone={r.zone} /></div>
            <div className="row" style={{ alignItems: 'flex-end', gap: 6, margin: '14px 0 18px' }}><span className="big-num">{r.score}</span><span className="c3" style={{ paddingBottom: 6 }}>из 100</span></div>
            <Gauge value={r.score} />
            <p className="t-sm" style={{ margin: '16px 0 4px', lineHeight: '20px' }}>{r.summary}</p>
            {r.factors.map((f) => (
              <div key={f.code} className={`factor ${f.state}`}><span className="code">{f.code}</span><div><div className="t">{f.title}</div><div className="e">{f.evidence}</div></div><span className="p">+{f.points}</span></div>
            ))}
            <div className="stack" style={{ marginTop: 16 }}>
              <button className="btn y full" onClick={() => notify('Кейс создан и добавлен в очередь')}><IcFlag size={20} />Создать кейс проверки</button>
              <div className="row" style={{ gap: 8 }}>
                <button className="btn g m" style={{ flex: 1 }} onClick={() => notify('Запрос сведений сформирован')}>Запросить сведения</button>
                <button className="btn g m" onClick={() => notify('Справка по объекту сформирована')} aria-label="Справка PDF"><IcDoc size={20} /></button>
              </div>
            </div>
            <Link to="/method" className="row t-xs c2" style={{ gap: 6, marginTop: 14 }}><IcInfo size={16} />ИИ даёт рекомендацию, решение принимает аналитик</Link>
          </div>
          <div className="card" style={{ height: 200, position: 'relative', overflow: 'hidden' }}>
            <MapView items={[{ c, zone: r.zone, score: r.score }]} center={[c.lng, c.lat]} zoom={14.5} interactive={false} labels />
          </div>
        </aside>
      </div>
    </>
  )
}
