import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowUpRight, Camera, ClipboardCheck, FileQuestion, Flag, MapPin } from 'lucide-react'
import MapView from '../components/MapView'
import Compare from '../components/Compare'
import { Disclaimer, Gauge, Highlight, NeedsCheck, PlatformTag, Score, StatusTag, TriMark, ZoneBadge, fmtDate, fmtDT } from '../components/ui'
import { developers } from '../data/demo'
import type { Tri } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

export default function ComplexCard() {
  const { id } = useParams()
  const { complexes, ads, notify } = useStore()
  const c = complexes.find((x) => x.id === id)
  const [tab, setTab] = useState<'ads' | 'photos' | 'passport'>('ads')
  if (!c) return <div className="empty">Объект не найден</div>
  const r = scoreComplex(c, ads)
  const dev = developers.find((d) => d.id === c.developerId)
  const linked = ads.filter((a) => a.complexId === c.id)

  const basisBox = (title: string, v: Tri, ref?: string, extra?: string) => (
    <div style={{ padding: '18px 20px', borderRight: '1px solid var(--line)' }} className="stack">
      <span className="label">{title}</span>
      <div className="display d-m" style={{ color: v === 'yes' ? 'var(--green)' : v === 'no' ? 'var(--red)' : 'var(--muted)' }}>{v === 'yes' ? 'Есть' : v === 'no' ? 'Нет' : 'Нет сведений'}</div>
      <span className="muted" style={{ fontSize: 12 }}>{ref ?? extra ?? (v === 'unknown' ? 'Запросить у уполномоченного органа' : '—')}</span>
    </div>
  )

  return (
    <div className="fade-in">
      <section className="hero" style={{ height: 520 }}>
        <img src={c.cover} alt="" />
        <div className="over" />
        <Link to="/complexes" className="btn sm" style={{ position: 'absolute', left: 32, top: 24, background: 'rgba(255,255,255,.9)', color: '#111', borderColor: 'transparent' }}><ArrowLeft size={13} />Реестр</Link>
        <div className="txt">
          <div style={{ flex: 1 }}>
            <div className="row" style={{ marginBottom: 18 }}><ZoneBadge zone={r.zone} pill />{r.needsCheck && <span className="check" style={{ background: 'rgba(0,0,0,.4)' }}>Нужна проверка</span>}</div>
            <div className="label" style={{ color: 'rgba(255,255,255,.7)', marginBottom: 10 }}>ЖК · {c.district} · {c.stage}</div>
            <h1 className="display d-xxl" style={{ color: '#fff' }}>{c.name}</h1>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div className="label" style={{ color: 'rgba(255,255,255,.7)' }}>Риск-скор</div>
            <div className="num" style={{ fontSize: 120, lineHeight: 0.85, color: '#fff' }}>{r.score}</div>
          </div>
        </div>
      </section>

      <div className="page" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 400px', gap: 48, paddingTop: 0 }}>
        <div>
          {/* Метки проекта — в духе JDS */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', borderBottom: '1px solid var(--line)' }}>
            {[['Застройщик', dev?.name], ['Адрес', c.address], ['Этажность', `${c.floors} эт. · ${c.flats} кв.`], ['Период', `${fmtDate(c.start)} — ${fmtDate(c.plannedEnd)}`], ['Готовность', `${c.progress}%`]].map(([k, v]) => (
              <div key={k} style={{ padding: '18px 16px 18px 0' }}><div className="label">{k}</div><div style={{ marginTop: 6, fontWeight: 500 }}>{v}</div></div>
            ))}
          </div>

          <h2 className="display d-l" style={{ margin: '44px 0 18px' }}>Основание <span className="serif-i">привлечения средств</span></h2>
          <div className="card" style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)' }}>
            {basisBox('Гарантия ЕО (КЖК)', c.basis.guarantee, c.basis.guaranteeRef)}
            {basisBox('Разрешение МИО', c.basis.mio, c.basis.mioRef)}
            {basisBox('Банк второго уровня', c.basis.bvu, c.basis.bank)}
            {basisBox('Перечень ГАСК', c.inPublishedList, c.inPublishedList === 'yes' ? 'Instagram ГАСК, 01.10.2026' : undefined)}
          </div>
          <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>Отсутствие отдельного разрешения МИО при действующей гарантии ЕО не является признаком R1. Отсутствие сведений — 0 баллов и отметка «Нужна проверка».</p>

          <div className="row" style={{ marginTop: 44, borderBottom: '1px solid var(--line-strong)', gap: 0 }}>
            {([['ads', `Реклама · ${linked.length}`], ['photos', `Фото-мониторинг · ${c.photos.length}`], ['passport', 'Застройщик']] as const).map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)} style={{ border: 0, background: 'transparent', padding: '12px 18px 12px 0', marginRight: 18, borderBottom: tab === k ? '2px solid var(--ink)' : '2px solid transparent', marginBottom: -1, color: tab === k ? 'var(--ink)' : 'var(--muted)' }} className="mono">{l}</button>
            ))}
          </div>

          {tab === 'ads' && (
            <div className="fade-in">
              {linked.length === 0 && <div className="empty">Публикаций по объекту не найдено</div>}
              {linked.map((a) => (
                <Link to={`/ads/${a.id}`} key={a.id} style={{ display: 'grid', gridTemplateColumns: '96px 1fr auto', gap: 18, padding: '18px 0', borderBottom: '1px solid var(--line)' }}>
                  {a.image ? <img src={a.image} alt="" style={{ width: 96, height: 96, objectFit: 'cover', filter: 'var(--img-filter)' }} /> : <div className="feed-noimg" style={{ width: 96, height: 96 }}>Aa</div>}
                  <div>
                    <div className="row" style={{ marginBottom: 6 }}><PlatformTag p={a.platform} /><span className="label">{a.account}</span><span className="label">{fmtDT(a.date)}</span></div>
                    <div className="ink2"><Highlight text={a.text} /></div>
                  </div>
                  <StatusTag s={a.status} />
                </Link>
              ))}
            </div>
          )}

          {tab === 'photos' && (
            <div className="fade-in" style={{ paddingTop: 24 }}>
              {c.photos.length >= 2 ? (
                <Compare before={c.photos[0]} after={c.photos[c.photos.length - 1]} />
              ) : c.photos.length === 0 ? (
                <div className="drop"><div className="stack" style={{ alignItems: 'center' }}><Camera size={22} /><div>Снимков нет. Загрузите фото с дрона или камеры</div><span className="label">JPG · PNG · дата съёмки из EXIF</span></div></div>
              ) : null}
              <div className="grid g-3" style={{ marginTop: 24 }}>
                {c.photos.map((p, i) => (
                  <div key={i}>
                    <div className="ph-frame" style={{ aspectRatio: '4/3' }}><img className="img" src={p.src} alt="" /></div>
                    <div className="cap"><span className="mono">{fmtDate(p.date)}</span><span className="label">{p.source}</span></div>
                    <div className="row wrap" style={{ gap: 10, paddingTop: 8 }}>
                      <span className={`zone z-${p.activity === 'есть' ? 'green' : p.activity === 'низкая' ? 'yellow' : 'red'}`}><i />Активность: {p.activity}</span>
                    </div>
                    <div className="row wrap" style={{ gap: 10, paddingTop: 6 }}>
                      {p.fence && <TriMark v={p.fence} label="Ограждение" />}{p.bins && <TriMark v={p.bins} label="Контейнер" />}{p.wheelWash && <TriMark v={p.wheelWash} label="Мойка колёс" />}
                    </div>
                    {p.note && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{p.note}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === 'passport' && dev && (
            <div className="fade-in grid g-2" style={{ paddingTop: 24 }}>
              {[['Наименование', dev.name], ['БИН', dev.bin], ['Руководитель', dev.director], ['Дата регистрации', fmtDate(dev.registered)], ['Объектов всего', dev.objects], ['Незавершённых', dev.unfinished], ['Сайт', dev.site ?? '—'], ['Instagram', dev.instagram ?? '—']].map(([k, v]) => (
                <div key={String(k)} className="cap"><span className="label">{k}</span><span>{v}</span></div>
              ))}
              <Link to="/developers" className="btn ghost" style={{ width: 'fit-content' }}>Профиль застройщика <ArrowUpRight size={14} /></Link>
            </div>
          )}
        </div>

        {/* Панель риска */}
        <aside style={{ position: 'sticky', top: 80, alignSelf: 'start', paddingTop: 28 }}>
          <div className="card card-pad stack" style={{ gap: 16 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}><span className="label">Объяснение скоринга</span><Score value={r.score} zone={r.zone} solid /></div>
            <Gauge value={r.score} />
            <div className="ink2">{r.summary}</div>
            {r.needsCheck && <div><NeedsCheck /></div>}
            <div>
              {r.factors.map((f) => (
                <div key={f.code} className={`factor ${f.state}`}>
                  <span className="code">{f.code}</span>
                  <div><div style={{ fontWeight: 500 }}>{f.title}</div><div className="muted" style={{ fontSize: 12, marginTop: 3 }}>{f.evidence}</div></div>
                  <span className="pts">+{f.points}</span>
                </div>
              ))}
            </div>
            <div className="stack" style={{ gap: 8 }}>
              <button className="btn primary" style={{ justifyContent: 'center' }} onClick={() => notify('Кейс создан и добавлен в очередь проверки')}><Flag size={14} />Создать кейс проверки</button>
              <div className="grid g-2" style={{ gap: 8 }}>
                <button className="btn ghost sm" onClick={() => notify('Запрос сведений сформирован')}><FileQuestion size={13} />Запросить сведения</button>
                <button className="btn ghost sm" onClick={() => notify('Отмечено как проверенное')}><ClipboardCheck size={13} />Проверено</button>
              </div>
            </div>
            <Disclaimer />
          </div>
          <div style={{ height: 220, marginTop: 16, position: 'relative', border: '1px solid var(--line)' }}>
            <MapView items={[{ c, zone: r.zone, score: r.score }]} center={[c.lng, c.lat]} zoom={14} interactive={false} />
            <span className="chip" style={{ position: 'absolute', left: 10, bottom: 10, zIndex: 2 }}><MapPin size={12} />{c.lat.toFixed(4)}, {c.lng.toFixed(4)}</span>
          </div>
        </aside>
      </div>
    </div>
  )
}
