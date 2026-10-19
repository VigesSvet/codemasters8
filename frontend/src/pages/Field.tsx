// Мобильный режим выезда (/field, после входа): геолокация → ближайшие ЖК → «Сделать снимок» камерой телефона → геометка, время,
// комментарий → снимок площадки в карточку ЖК («Снимки») и Фото-мониторинг (R6). Загрузка — тот же POST /api/complexes/{id}/photos,
// что у окна «Добавить снимок площадки»; геометка и расстояние до точки ЖК — в примечании снимка. Офлайн-очереди нет.
// Стили — styles/field.css; манифест — public/manifest.webmanifest (страницу можно добавить на экран «Домой»)
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import LangSwitch from '../components/LangSwitch'
import type { Complex } from '../data/types'
import { errorText } from '../lib/api'
import { FAR_M, geotagNote, isFar, localStamp, nearest, shotDistance, type Here } from '../lib/field'
import { findComplex } from '../lib/find'
import { fmtDist, fmtPoint } from '../lib/geo'
import { roleIn, useT } from '../lib/i18n'
import { useStore } from '../lib/store'
import { photoFields, todayLocal, validatePhoto, type Activity, type PhotoForm } from '../lib/verification'
import { IcCamera, IcCheck, IcChevronLeft, IcChevronRight, IcLocate, IcSearch, IcWarning } from '../ui/icons'
import '../styles/field.css'

type Geo = { state: 'idle' | 'wait' | 'ok' | 'denied' | 'error' | 'unsupported'; here?: Here; err?: string }
type Shot = { file: File; url: string; takenAt: string; at: Here | null; geoWait: boolean }
const ACTS: Activity[] = ['active', 'idle', 'unknown']
const GEO_OPTS: PositionOptions = { enableHighAccuracy: true, timeout: 15000, maximumAge: 20000 }
const toHere = (p: GeolocationPosition): Here => ({ lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy })

/** Текущее местоположение одним запросом; нет API — unsupported */
function position(fresh = false): Promise<Here> {
  return new Promise((ok, fail) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) { fail(Object.assign(new Error('unsupported'), { code: -1 })); return }
    navigator.geolocation.getCurrentPosition((p) => ok(toHere(p)), fail, fresh ? { ...GEO_OPTS, maximumAge: 0 } : GEO_OPTS)
  })
}

export default function Field() {
  const { complexes, developers, user, mode } = useStore()
  const i = useT()
  const { t, tn } = i
  const [geo, setGeo] = useState<Geo>({ state: 'wait' })
  const [q, setQ] = useState('')
  const [sp, setSp] = useSearchParams()
  const sel = complexes.find((c) => c.id === sp.get('c'))

  const request = () => position().then(
    (here) => setGeo({ state: 'ok', here }),
    (e: GeolocationPositionError | Error & { code?: number }) => setGeo((g) => ({
      here: g.here, state: e.code === -1 ? 'unsupported' : e.code === 1 ? 'denied' : 'error', err: e.message,
    })),
  )
  const locate = () => { setGeo((g) => ({ ...g, state: 'wait' })); void request() }
  useEffect(() => { void request() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const dev = useMemo(() => new Map(developers.map((d) => [d.id, d])), [developers])
  const rows = useMemo(() => nearest(complexes, geo.here ?? null).filter((x) => !q.trim() || findComplex(q, x.c, dev.get(x.c.developerId))), [complexes, geo.here, q, dev])
  const role = user ? roleIn(i, user).title : ''

  return (
    <div className="fld">
      <header className="fld-top">
        <Link to="/" className="fld-logo" title={t('fld.title')}><img src="/logo.svg" alt="" /><span>{t('fld.short')}</span></Link>
        <LangSwitch />
        <Link to="/" className="fld-full">{t('fld.full')}</Link>
      </header>

      {sel ? <Site c={sel} here={geo.here ?? null} role={role} live={mode === 'api'} onBack={() => setSp({}, { replace: false })} /> : (
        <main className="fld-main">
          <section className="fld-geo">
            <div className="fld-geo-t">
              {geo.state === 'wait' && <span>{t('fld.locating')}</span>}
              {geo.here && geo.state !== 'wait' && <span>{t('fld.here', { p: fmtPoint(geo.here), a: Math.round(geo.here.acc ?? 0) })}</span>}
              {geo.state === 'denied' && <span className="warn">{t('fld.geoDenied')}</span>}
              {geo.state === 'error' && <span className="warn">{t('fld.geoNone', { e: geo.err ?? '' })}</span>}
              {geo.state === 'unsupported' && <span className="warn">{t('fld.geoUnsupported')}</span>}
            </div>
            <button type="button" className="fld-btn g" onClick={locate} disabled={geo.state === 'wait'}><IcLocate size={18} />{geo.here ? t('fld.relocate') : t('fld.locate')}</button>
          </section>
          <label className="fld-search"><IcSearch size={18} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('common.search')} aria-label={t('common.search')} /></label>
          <h2 className="fld-h">{geo.here ? t('fld.nearest') : t('fld.byName')}</h2>
          <ul className="fld-list">
            {rows.map(({ c, m }) => {
              const last = [...c.photos].sort((a, b) => b.date.localeCompare(a.date))[0]
              return (
                <li key={c.id}>
                  <button type="button" onClick={() => setSp({ c: c.id })}>
                    <span className="fld-dist">{m != null ? fmtDist(m) : '—'}</span>
                    <span className="fld-name"><b>{c.name}</b><small>{c.address || t('common.noAddress')}</small>
                      <small>{c.photos.length ? `${tn('fld.shots', c.photos.length)} · ${t('fld.lastShot', { d: i.dayMonth(last.date) })}` : t('fld.noShots')}</small></span>
                    <IcChevronRight size={18} />
                  </button>
                </li>
              )
            })}
          </ul>
          <p className="fld-note">{t('fld.install')}</p>
        </main>
      )}
    </div>
  )
}

/** ЖК на выезде: расстояние, снимок камерой с геометкой, активность, комментарий, загрузка */
function Site({ c, here, role, live, onBack }: { c: Complex; here: Here | null; role: string; live: boolean; onBack: () => void }) {
  const { addSitePhoto } = useStore()
  const i = useT()
  const { t, tn } = i
  const input = useRef<HTMLInputElement>(null)
  const [shot, setShot] = useState<Shot | null>(null)
  const [act, setAct] = useState<Activity | ''>('')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const url = shot?.url
  useEffect(() => () => { if (url) URL.revokeObjectURL(url) }, [url])

  const toPoint = here ? shotDistance(c, here) : null
  const m = shot ? shotDistance(c, shot.at) : null

  const onFile = (file: File | undefined) => {
    if (!file) return
    const takenAt = new Date().toISOString()
    setShot({ file, url: URL.createObjectURL(file), takenAt, at: null, geoWait: true })
    setErr(null); setDone(false)
    // геометка — местоположение телефона в момент снимка (EXIF камеры браузеры часто вырезают)
    position(true).then(
      (at) => setShot((s) => (s && s.takenAt === takenAt ? { ...s, at, geoWait: false } : s)),
      () => setShot((s) => (s && s.takenAt === takenAt ? { ...s, at: here, geoWait: false } : s)),
    )
  }

  const upload = async () => {
    if (!shot) return
    const note = geotagNote({ at: shot.at, takenAt: shot.takenAt, m, comment })
    const form: PhotoForm = { file: shot.file, date: todayLocal(Date.parse(shot.takenAt)), choice: 'Выезд на площадку', other: '', activity: act, note }
    const e = validatePhoto(form)
    const first = e.file ?? e.activity ?? e.date ?? e.comment
    if (first) { setErr(e.activity ? t('fld.pickActivity') : first); return }
    setBusy(true); setErr(null)
    try {
      await addSitePhoto(c.id, shot.file, photoFields(form, ''))
      setDone(true); setShot(null); setAct(''); setComment('')
    } catch (x) {
      setErr(errorText(x))
    } finally { setBusy(false) }
  }

  return (
    <main className="fld-main">
      <button type="button" className="fld-back" onClick={onBack}><IcChevronLeft size={16} />{t('fld.back')}</button>
      <section className="fld-card">
        <h1>{c.name}</h1>
        <p className="fld-addr">{c.address || t('common.noAddress')}</p>
        <p className="fld-meta">{toPoint != null && <b>{t('fld.dist', { d: fmtDist(toPoint) })}</b>}<span>{c.photos.length ? tn('fld.shots', c.photos.length) : t('fld.noShots')}</span></p>
        <p className="fld-note">{t('fld.r6')}</p>
      </section>

      {done && (
        <section className="fld-ok" role="status">
          <IcCheck size={22} strokeWidth={2.6} /><b>{t('fld.done')}</b>
          <div className="fld-row"><Link to={`/complexes/${c.id}?tab=photos`} className="fld-btn g">{t('fld.openCard')}</Link><Link to="/sites" className="fld-btn g">{t('nav.sites')}</Link></div>
        </section>
      )}

      <input ref={input} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = '' }} />
      {!shot ? (
        <button type="button" className="fld-btn y big" onClick={() => input.current?.click()} disabled={!live} title={live ? undefined : t('fld.offline')}>
          <IcCamera size={22} />{done ? t('fld.another') : t('fld.take')}
        </button>
      ) : (
        <section className="fld-card">
          <img className="fld-shot" src={shot.url} alt="" />
          <p className="fld-meta"><span>{t('fld.takenAt', { t: localStamp(shot.takenAt) })}</span></p>
          <p className="fld-meta">
            {shot.geoWait ? <span>{t('fld.geoShotWait')}</span>
              : shot.at ? <span>{t('fld.geoShot', { p: fmtPoint(shot.at), a: Math.round(shot.at.acc ?? 0) })}</span> : <span>{t('fld.geoShotNone')}</span>}
            {m != null && <b className={isFar(m) ? 'far' : ''}>{t('fld.dist', { d: fmtDist(m) })}</b>}
          </p>
          {isFar(m) && <p className="fld-warn"><IcWarning size={18} />{t('fld.far', { m: FAR_M })}</p>}
          <div className="fld-field">
            <span>{t('fld.activity')}</span>
            <div className="fld-seg" role="radiogroup" aria-label={t('fld.activity')}>
              {ACTS.map((a) => <button key={a} type="button" role="radio" aria-checked={act === a} className={act === a ? 'on' : ''} onClick={() => setAct(a)}>{t(`fld.act.${a}`)}</button>)}
            </div>
          </div>
          <label className="fld-field"><span>{t('fld.comment')}</span>
            <textarea value={comment} maxLength={1500} onChange={(e) => setComment(e.target.value)} placeholder={t('fld.commentPh')} rows={3} />
          </label>
          {err && <p className="fld-err" role="alert">{err}</p>}
          <div className="fld-row">
            <button type="button" className="fld-btn g" onClick={() => input.current?.click()} disabled={busy}><IcCamera size={18} />{t('fld.retake')}</button>
            <button type="button" className="fld-btn y" onClick={upload} disabled={busy || shot.geoWait || !live}>{busy ? t('fld.uploading') : t('fld.upload')}</button>
          </div>
          <p className="fld-note">{t('fld.who', { role })}</p>
        </section>
      )}
      {!live && <p className="fld-note warn">{t('fld.offline')}</p>}
    </main>
  )
}
