// «Ситуационный центр» (/tv, после входа) — экран для ТВ: тёмная панель на весь экран, слайды сменяются каждые 12 с:
// карта города с зонами (MapView без управления), сводка (зоны, очередь, сигналы, «Проверка по рекламе»), свежая реклама
// с предложением оплаты, сигналы ждут решения, стройка стоит. Часы, «данные на ЧЧ:ММ», обновление данных раз в 60 с
// (refreshAds / refreshSignals / refreshScreening стора), ←/→ — слайды, пробел — пауза, курсор прячется. Стили — styles/tv.css
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import MapView, { type MapItem } from '../components/MapView'
import { Emblem, type EmblemName, type EmTone } from '../components/emblems'
import { Highlight, PlatformIcon } from '../components/ui'
import type { ScreeningStatus, Zone } from '../data/types'
import { isPaymentOffer } from '../lib/hotspots'
import { useT, type Key } from '../lib/i18n'
import { isAssessed, linkConfirmed } from '../lib/risk'
import { DISCLAIMER_SHORT, SCREENING_GLYPH, SCREENING_ORDER, countByStatus, hasPoint, statusOf } from '../lib/screening'
import { useStore } from '../lib/store'
import '../styles/tv.css'

const SLIDE_MS = 12000
const REFRESH_MS = 60000
const IDLE_DAYS = 30
const SLIDES: Key[] = ['tv.s.map', 'tv.s.stats', 'tv.s.offers', 'tv.s.signals', 'tv.s.idle']
const ZONES: Zone[] = ['red', 'yellow', 'green']
const SCR_TONE: Record<ScreeningStatus, EmTone> = { ok: 'green', check: 'yellow', suspect: 'red', unknown: 'gray' }

/** Следующий слайд по кругу: d = +1 вперёд, −1 назад */
const step = (i: number, d: number, n = SLIDES.length) => (((i + d) % n) + n) % n

function Tile({ em, tone, value, label, sub }: { em: EmblemName; tone?: EmTone; value: number | string; label: string; sub?: string }) {
  return (
    <div className={`tv-tile ${tone ?? ''}`}>
      <Emblem name={em} tone={tone} size={56} />
      <b>{typeof value === 'number' ? value.toLocaleString('ru-RU') : value}</b>
      <span>{label}</span>
      {sub && <small>{sub}</small>}
    </div>
  )
}

export default function Tv() {
  const { complexes, ads, signals, stats, riskOf, lastSync, mode, refreshAds, refreshSignals, refreshScreening } = useStore()
  const i = useT()
  const { t, tr, locale } = i
  const [idx, setIdx] = useState(0)
  const [paused, setPaused] = useState(false)
  const [now, setNow] = useState(() => new Date())
  const [idle, setIdle] = useState(false)

  // часы
  useEffect(() => { const h = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(h) }, [])
  // смена слайда; пауза — пробел
  useEffect(() => {
    if (paused) return
    const h = setTimeout(() => setIdx((x) => step(x, 1)), SLIDE_MS)
    return () => clearTimeout(h)
  }, [idx, paused])
  // данные — раз в минуту, без баннеров ошибок (на ТВ их некому закрыть)
  useEffect(() => {
    if (mode !== 'api') return
    const h = setInterval(() => { void refreshAds({ quiet: true }); void refreshSignals(); void refreshScreening() }, REFRESH_MS)
    return () => clearInterval(h)
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps
  // клавиатура: ← → и пробел
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') setIdx((x) => step(x, 1))
      else if (e.key === 'ArrowLeft') setIdx((x) => step(x, -1))
      else if (e.key === ' ' || e.code === 'Space') { e.preventDefault(); setPaused((p) => !p) }
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [])
  // курсор прячется через 2,5 с без движения мыши
  useEffect(() => {
    let h = setTimeout(() => setIdle(true), 2500)
    const move = () => { setIdle(false); clearTimeout(h); h = setTimeout(() => setIdle(true), 2500) }
    window.addEventListener('mousemove', move)
    return () => { clearTimeout(h); window.removeEventListener('mousemove', move) }
  }, [])

  const scored = useMemo(() => complexes.map((c) => { const r = riskOf(c); return { c, r, off: !isAssessed(r), zone: r.zone, score: r.score, screen: statusOf(c) } }), [complexes, riskOf])
  const items: MapItem[] = scored.filter((x) => hasPoint(x.c))
  const zone = (z: Zone) => scored.filter((x) => !x.off && x.zone === z).length
  const off = scored.filter((x) => x.off).length
  const cx = stats?.screening?.complexes ?? countByStatus(complexes, statusOf)
  const queue = ads.filter((a) => a.status === 'new' || a.status === 'needs_check').length
  const waiting = signals.filter((s) => s.status === 'new').sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  const offers = useMemo(() => ads.filter(isPaymentOffer).sort((a, b) => b.date.localeCompare(a.date)), [ads])
  const stalled = scored.filter((x) => x.c.idleDays >= IDLE_DAYS).sort((a, b) => b.c.idleDays - a.c.idleDays)
  const name = (id?: string) => complexes.find((c) => c.id === id)?.name
  const hhmm = (d: Date) => d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
  const full = () => { if (document.fullscreenElement) void document.exitFullscreen(); else void document.documentElement.requestFullscreen?.() }

  return (
    <div className={`tv ${idle ? 'idle' : ''}`}>
      <header className="tv-top">
        <Link to="/" className="tv-logo"><img src="/logo.svg" alt="" />SAQ Qurylys</Link>
        <div className="tv-title"><span>{t('tv.title')} · {t('common.city')}</span><b>{t(SLIDES[idx])}</b></div>
        <div className="tv-dots" role="tablist" aria-label={t('tv.title')}>
          {SLIDES.map((s, n) => <button key={s} type="button" role="tab" aria-selected={n === idx} aria-label={t('tv.slide', { n: n + 1 })} className={n === idx ? 'on' : ''} onClick={() => setIdx(n)} />)}
        </div>
        <div className="tv-clock">
          <b>{now.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</b>
          <span>{lastSync ? t('tv.dataAt', { t: hhmm(new Date(lastSync)) }) : t('common.loading')}{paused && <em> · {t('tv.paused')}</em>}</span>
        </div>
        <button type="button" className="tv-fs" onClick={full}>{t('tv.full')}</button>
      </header>

      <main className="tv-stage" key={idx}>
        {idx === 0 && (
          <div className="tv-map">
            <div className="tv-mapbox"><MapView items={items} interactive={false} labels fit zoom={12} /></div>
            <aside className="tv-side">
              <div className="tv-panel"><div className="tv-li"><Emblem name="city" size={44} /><span>{t('tv.onControl')}</span><b>{scored.length}</b></div></div>
              <div className="tv-panel">
                <h3>{t('scr.title')}</h3>
                {SCREENING_ORDER.map((k) => <div key={k} className="tv-li"><span className={`tv-g ${k}`}>{SCREENING_GLYPH[k]}</span><span>{t(`scr.filter.${k}`)}</span><b>{cx[k]}</b></div>)}
              </div>
              <div className="tv-panel">
                <h3>{t('tv.zones')}</h3>
                {ZONES.map((z) => <div key={z} className="tv-li"><span className={`tv-z ${z}`} /><span>{t(`zone.name.${z}`)}</span><b>{zone(z)}</b></div>)}
                <div className="tv-li"><span className="tv-z off" /><span>{t('tv.notAssessed')}</span><b>{off}</b></div>
              </div>
            </aside>
          </div>
        )}

        {idx === 1 && (
          <div className="tv-stats">
            <Tile em="city" value={scored.length} label={t('tv.onControl')} />
            <Tile em="inbox" tone="blue" value={queue} label={t('tv.queue')} />
            <Tile em="flag" tone="red" value={waiting.length} label={t('tv.s.signals')} />
            <Tile em="hourglass" tone="yellow" value={stalled.length} label={t('tv.idleN')} />
            <Tile em="feed" value={ads.length} label={t('tv.ads')} sub={`${offers.length.toLocaleString('ru-RU')} ${t('tv.offersN')}`} />
            <div className="tv-panel wide">
              <h3>{t('scr.title')}</h3>
              <div className="tv-bar">{SCREENING_ORDER.filter((k) => cx[k] > 0).map((k) => <i key={k} className={k} style={{ flexGrow: cx[k] }} />)}</div>
              <div className="tv-legend">{SCREENING_ORDER.map((k) => <span key={k}><Emblem name="review" tone={SCR_TONE[k]} size={28} />{t(`scr.filter.${k}`)}<b>{cx[k]}</b></span>)}</div>
              <p className="tv-disc">{tr(DISCLAIMER_SHORT)}</p>
            </div>
            <div className="tv-panel wide">
              <h3>{t('tv.zones')}</h3>
              <div className="tv-bar">{ZONES.filter((z) => zone(z) > 0).map((z) => <i key={z} className={z} style={{ flexGrow: zone(z) }} />)}{off > 0 && <i className="off" style={{ flexGrow: off }} />}</div>
              <div className="tv-legend">{ZONES.map((z) => <span key={z}><span className={`tv-z ${z}`} />{t(`zone.name.${z}`)}<b>{zone(z)}</b></span>)}<span><span className="tv-z off" />{t('tv.notAssessed')}<b>{off}</b></span></div>
            </div>
          </div>
        )}

        {idx === 2 && (
          offers.length ? (
            <div className="tv-cards">
              {offers.slice(0, 6).map((a) => (
                <article key={a.id} className="tv-ad">
                  <header><PlatformIcon p={a.platform} size={30} /><span className="acc">{a.account}</span><time>{i.dayMonth(a.date)}</time></header>
                  <p><Highlight text={a.text} /></p>
                  {/* ЖК — привязка публикации, а без неё — лучший кандидат Claude; и то и другое до решения аналитика — гипотеза */}
                  {(() => { const cand = name(a.complexId) ?? a.llm?.complexCandidates?.[0]?.complexName; return (
                    <footer><b>{cand ?? t('tv.noComplex')}</b>{cand && !(a.complexId && linkConfirmed(a)) && <em>{t('tv.aiLink')}</em>}</footer>
                  ) })()}
                </article>
              ))}
            </div>
          ) : <p className="tv-empty">{t('tv.noOffers')}</p>
        )}

        {idx === 3 && (
          waiting.length ? (
            <div className="tv-rows">
              {waiting.slice(0, 8).map((s) => (
                <div key={s.id} className="tv-row"><span className={`tv-z ${s.zone}`} /><b>{s.code}</b><span className="nm">{s.complexName}</span><span>{t('tv.score', { n: s.score })}</span><time>{t('tv.created', { d: i.dayMonth(s.createdAt) })}</time></div>
              ))}
            </div>
          ) : <p className="tv-empty">{t('tv.noSignals')}</p>
        )}

        {idx === 4 && (
          stalled.length ? (
            <div className="tv-rows">
              {stalled.slice(0, 8).map((x) => (
                <div key={x.c.id} className="tv-row"><span className={`tv-z ${x.off ? 'off' : x.zone}`} /><b>{x.c.name}</b><span className="nm">{x.c.address}</span><span>{t('tv.idleDays', { n: x.c.idleDays })}</span></div>
              ))}
            </div>
          ) : <p className="tv-empty">{t('tv.noIdle')}</p>
        )}
      </main>

      <footer className="tv-foot">
        <div className={`tv-prog ${paused ? 'paused' : ''}`} key={`${idx}-${paused}`} style={{ animationDuration: `${SLIDE_MS}ms` }} />
        <span>{t('tv.keys')}</span>
      </footer>
    </div>
  )
}
