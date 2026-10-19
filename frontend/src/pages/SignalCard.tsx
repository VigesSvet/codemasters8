import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AuditList } from '../components/Audit'
import { EvidenceView, FactorList, LawNote, ObservationList } from '../components/Factors'
import { MediaViewer } from '../components/MediaViewer'
import { RequestLetterButton } from '../components/RequestLetter'
import { RoutePanel } from '../components/SignalRoute'
import { Origin } from '../components/Provenance'
import { SignalGate, SignalStatusTag, SignalTime } from '../components/SignalPanel'
import { Cell, Gauge, Highlight, Modal, NeedsCheck, PageTitle, PlatformIcon, RiskScore, RiskZone, SafeImg, Score, StatusTag, ZoneTag, adKey, fmtDT, fmtDate, zoneName } from '../components/ui'
import type { Appeal, EvidenceItem, ReadinessValue, Signal, SignalAd } from '../data/types'
import { ApiError } from '../lib/api'
import { mediaSrc } from '../lib/media'
import { from, useFrom, type From } from '../lib/nav'
import { externalUrl } from '../lib/platform'
import { DEMO_ORG, isDemo } from '../lib/provenance'
import { isAssessed, linkConfirmed } from '../lib/risk'
import { useStore } from '../lib/store'
import { adGallery } from '../lib/viewer'
import { IcBuilding, IcCheck, IcDoc, IcDownload, IcExternal, IcShield, IcZoomIn } from '../ui/icons'

type Decision = 'confirm' | 'return'

const appealItem = (a: Appeal): EvidenceItem =>
  ({ kind: 'appeal', ref: a.id, label: `${a.subject} · заявителей: ${a.applicants}`, value: a.status, source: a.source, date: a.date })
const zoneInk = (z: Signal['zone']) => `var(--${z === 'yellow' ? 'amber' : z})`
const COMMENT_MAX = 4000
const origin = (v: ReadinessValue) => [v.source, v.date && fmtDate(v.date)].filter(Boolean).join(', ') || 'без источника'

/** Сигнальная карточка (модуль 05): снимок риска и доказательств по ЖК, решение аналитика, журнал, выгрузка */
export default function SignalCard() {
  const { id } = useParams()
  const [ver, setVer] = useState(0)
  return <SignalGate id={id} version={ver}>{(sig, { set }) => <Card key={sig.id} sig={sig} set={set} reload={() => setVer((v) => v + 1)} />}</SignalGate>
}

function Card({ sig, set, reload }: { sig: Signal; set: (s: Signal) => void; reload: () => void }) {
  const { complexes, riskOf, user, decideSignal, refreshSignals, signalPdfUrl, notify } = useStore()
  // откуда пришли (реестр с фильтром, сводка) — туда и ведёт крошка; иначе — весь реестр сигналов
  const came = useFrom()
  const [decision, setDecision] = useState<Decision | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  const ev = sig.evidence, c = ev.complex, dev = ev.developer, rd = c.readiness
  const live = complexes.find((x) => x.id === sig.complexId)
  const now = live ? riskOf(live) : null
  const drift = now && (!isAssessed(now) || now.score !== sig.score || now.zone !== sig.zone)
  // публикации-доказательства открываются с «← Сигнал S-…»: разбор ведёт обратно сюда
  const back: From = { label: `Сигнал ${sig.code}`, to: `/signals/${sig.id}` }

  const close = () => { setDecision(null); setComment('') }
  const submit = async () => {
    if (!decision || !comment.trim()) return
    setBusy(true)
    try {
      const s = await decideSignal(sig.id, decision, comment.trim())
      set(s)
      close()
      notify(decision === 'confirm' ? `Сигнал ${s.code} подтверждён, решение записано в журнал` : `Сигнал ${s.code} возвращён на доработку`)
      void refreshSignals() // ошибку обновления реестра стор покажет баннером
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e))
      // уже рассмотрен другим аналитиком — показываем актуальное состояние
      if (e instanceof ApiError && e.status === 409) { close(); reload(); void refreshSignals() }
    } finally {
      setBusy(false)
    }
  }
  // PDF отдаёт сервер и сам пишет выгрузку в журнал — перечитываем сигнал чуть позже
  const exported = () => {
    notify(`Скачиваем ${sig.fileName}`)
    clearTimeout(timer.current)
    timer.current = setTimeout(reload, 1500)
  }

  return (
    <>
      <PageTitle crumbs={came ? [[`← ${came.label}`, came.to], [sig.code]] : [['Сигналы', '/signals'], [sig.code]]}
        title={<span className="row wrap-f" style={{ gap: 14 }}>Сигнал {sig.code} · ЖК «{c.name}»<ZoneTag zone={sig.zone} /><SignalStatusTag s={sig.status} />{isDemo(c) && <Origin kind="demo" label="Демо-сценарий (вымышленный ЖК)" />}</span>}
        sub={<>Создан: {sig.createdBy}, <SignalTime at={sig.createdAt} />. Данные — снимок на момент создания, дальше не пересчитываются.</>} />

      <div className="cols-r">
        <div className="stack" style={{ gap: 16 }}>
          <section className="card">
            <div className="card-head"><h3>Объект</h3><Link to={`/complexes/${c.id}`} state={from(back.label, back.to)} className="link t-sm">Карточка ЖК</Link></div>
            <div className="sig-obj">
              {c.cover && <img src={c.cover} alt="" />}
              <div className="kv">
                <span>ЖК</span><span><Link className="link" to={`/complexes/${c.id}`} state={from(back.label, back.to)}>ЖК «{c.name}»</Link></span>
                <span>Адрес</span><span>{c.address}{c.district && <span className="c2"> · {c.district}</span>}</span>
                <span>Застройщик</span><span>{dev ? <>{dev.name}<span className="c2 num"> · БИН {dev.bin}</span>{isDemo(dev) && <span className="c2"> · {DEMO_ORG}</span>}</> : <span className="c2">нет данных</span>}</span>
                <span>Стадия</span><span>{c.stage || 'не известна'}, {rd?.confirmed
                  ? <>подтверждённая готовность {rd.confirmed.value}%<span className="c2"> · {origin(rd.confirmed)}</span></>
                  : <>готовность по карточке объекта {c.progress}%<span className="c2"> · без источника</span></>}
                  {rd?.declared && <span className="c2"> · заявлено {rd.declared.value}%</span>}</span>
                <span>Срок сдачи</span><span>{c.plannedEnd ? fmtDate(c.plannedEnd) : 'не указан'}{c.start && <span className="c2"> · начало {fmtDate(c.start)}</span>}</span>
              </div>
            </div>
            <h4 className="sig-h">Основание привлечения средств</h4>
            {ev.documents.length ? <div className="ev-list">{ev.documents.map((d, i) => <EvidenceView key={i} item={d} />)}</div> : <p className="t-sm c3">Документов-оснований в снимке нет</p>}
          </section>

          <section className="card">
            <div className="card-head"><h3>Риск на момент сигнала</h3><span className="t-sm c2">модель {sig.modelVersion}</span></div>
            <div className="sig-score">
              <div className="row" style={{ alignItems: 'flex-end', gap: 6 }}><span className="big-num" style={{ color: zoneInk(sig.zone) }}>{sig.score}</span><span className="c3" style={{ paddingBottom: 4 }}>из 100</span></div>
              <div style={{ flex: 1, maxWidth: 420 }}><Gauge value={sig.score} /></div>
            </div>
            <p style={{ marginTop: 16, lineHeight: '21px' }}>{sig.summary}</p>
            {sig.needsCheck && <div style={{ marginTop: 10 }}><NeedsCheck /></div>}
            {drift && now && (
              <div className="sig-drift">
                Сейчас у объекта:<RiskScore r={now} /><RiskZone r={now} />
                <Link to={`/complexes/${c.id}?tab=risk`} state={from(back.label, back.to)} className="link" style={{ marginLeft: 'auto' }}>Открыть карточку ЖК</Link>
              </div>
            )}
            <div style={{ marginTop: 8 }}><FactorList factors={sig.factors} from={back} /></div>
            <LawNote />
          </section>

          <section className="card">
            <div className="card-head"><h3>Доказательства</h3><span className="t-sm c2">на {fmtDT(sig.createdAt)}</span></div>
            <h4 className="sig-h">Публикации · {ev.ads.length}</h4>
            {ev.ads.length ? ev.ads.map((a) => <SigAd key={a.id} a={a} back={back} />) : <p className="t-sm c3">Привязанных публикаций нет</p>}
            <h4 className="sig-h">Обращения граждан · {ev.appeals.length}</h4>
            {ev.appeals.length ? <div className="ev-list">{ev.appeals.map((a) => <EvidenceView key={a.id} item={appealItem(a)} />)}</div> : <p className="t-sm c3">Обращений нет</p>}
            <h4 className="sig-h">Снимки стройки · {ev.photos.length}</h4>
            {ev.photos.length ? (
              <div className="sig-photos">
                {ev.photos.map((p, i) => (
                  <figure key={i}>
                    <div className="ph"><img src={mediaSrc(p.src) ?? p.src} alt="" /></div>
                    <figcaption><b>{fmtDate(p.date)}</b><span>{p.source} · активность: {p.activity}</span>{p.note && <span>{p.note}</span>}</figcaption>
                  </figure>
                ))}
              </div>
            ) : <p className="t-sm c3">Снимков нет</p>}
          </section>

          {sig.observations.length > 0 && (
            <section className="card">
              <div className="card-head"><h3>Вне балла</h3></div>
              <p className="t-sm c2">Наблюдения не влияют на балл и зону — это контекст для аналитика.</p>
              <ObservationList items={sig.observations} />
            </section>
          )}

          <section className="card">
            <div className="card-head"><h3>Журнал решений</h3><span className="t-sm c2">{sig.journal.length}</span></div>
            <AuditList events={sig.journal} links={['ad']} from={back} />
          </section>

          <p className="row t-sm c2" style={{ gap: 8, alignItems: 'flex-start' }}><IcShield size={16} />Сигнал — рекомендация для проверки, не юридическое заключение. Автоматической отправки в госорганы нет.{isDemo(c) && ' Данные демонстрационные.'}</p>
        </div>

        <aside className="stack sticky">
          <div className="card">
            <div className="row between"><span className="t-h3">Статус проверки</span><SignalStatusTag s={sig.status} /></div>
            <div className="kv" style={{ marginTop: 14, gridTemplateColumns: '110px 1fr' }}>
              <span>Создан</span><span>{sig.createdBy}<div className="t-sm c2"><SignalTime at={sig.createdAt} /></div></span>
              <span>Решение</span><span>{sig.decidedAt ? <>{sig.decidedBy}<div className="t-sm c2"><SignalTime at={sig.decidedAt} /></div></> : <span className="c2">ещё не принято</span>}</span>
              {sig.decisionComment && <><span>Комментарий</span><span style={{ overflowWrap: 'anywhere' }}>{sig.decisionComment}</span></>}
              <span>Риск</span><span className="row" style={{ gap: 8 }}><Score value={sig.score} zone={sig.zone} />{sig.hits.length ? sig.hits.join(', ') : 'без подтверждённых факторов'}</span>
              <span>Модель</span><span>{sig.modelVersion}</span>
            </div>
            {sig.status === 'new' && (
              <div className="stack" style={{ gap: 8, marginTop: 20 }}>
                <button className="btn y full" onClick={() => setDecision('confirm')}><IcCheck />Подтвердить сигнал</button>
                <button className="btn g full" onClick={() => setDecision('return')}>Вернуть на доработку</button>
              </div>
            )}
            <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>Решение принимает аналитик. Автор, дата, время и комментарий фиксируются в журнале.</p>
          </div>

          <RoutePanel sig={sig} onChanged={() => { reload(); void refreshSignals() }} />

          <div className="card">
            <div className="t-h3">Выгрузка</div>
            <div className="stack" style={{ gap: 8, marginTop: 12 }}>
              <a className="btn g m full" href={signalPdfUrl(sig.id)} download={sig.fileName} onClick={exported}><IcDownload />Скачать PDF</a>
              <Link className="btn g m full" to={`/signals/${sig.id}/print`} target="_blank" rel="noopener"><IcDoc />Печатная версия</Link>
              <RequestLetterButton complexId={c.id} complexName={c.name} className="btn g m full" label="Запрос в ЖАО / ЕО" />
            </div>
            <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>
              {sig.exportedAt ? <>PDF выгружен: <SignalTime at={sig.exportedAt} />. </> : 'PDF ещё не выгружался. '}
              Для внутреннего маршрута — автоматической отправки в госорганы нет.
            </p>
          </div>
        </aside>
      </div>

      {decision && (
        <Modal title={decision === 'confirm' ? `Подтвердить сигнал ${sig.code}?` : `Вернуть ${sig.code} на доработку?`} onClose={close}
          footer={<>
            <button className="btn g" onClick={close}>Отмена</button>
            <button className={`btn ${decision === 'confirm' ? 'y' : 'd'}`} disabled={!comment.trim() || busy} onClick={submit}>
              {busy ? 'Сохраняем…' : decision === 'confirm' ? 'Подтвердить сигнал' : 'Вернуть на доработку'}
            </button>
          </>}>
          <Cell icon={<span className="ico"><IcBuilding size={20} /></span>} title={`ЖК «${c.name}»`} sub={`балл ${sig.score} · ${zoneName[sig.zone].toLowerCase()}`} />
          <Cell icon={<span className="ava">{user?.short}</span>} title={user?.name} sub={`${user?.role} · автор решения, дата и время попадут в журнал`} />
          <div style={{ padding: '12px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="sigc">{decision === 'confirm' ? 'Основание решения' : 'Что нужно доработать'} — обязательно, попадёт в журнал и PDF</label>
            <textarea id="sigc" className="input" style={{ marginTop: 6 }} autoFocus maxLength={COMMENT_MAX} value={comment} onChange={(e) => setComment(e.target.value)}
              placeholder={decision === 'confirm'
                ? 'Например: сверено с картотекой ГАСК — гарантии ЕО и разрешения МИО нет, в рекламе предлагают внести первый взнос'
                : 'Например: привязка публикации к ЖК не подтверждена, запросить сведения о разрешении МИО'} />
            <div className="t-xs c2 num" style={{ marginTop: 4, textAlign: 'right' }}>{comment.length} / {COMMENT_MAX}</div>
          </div>
        </Modal>
      )}
    </>
  )
}

/** Публикация из снимка: платформа, аккаунт, статус, уверенность привязки, текст с подсветкой, ссылки */
function SigAd({ a, back }: { a: SignalAd; back: From }) {
  const fact = linkConfirmed(a)
  // снимок публикации в сигнале: обложка — через mediaSrc (серверные /api/media/… учитывают VITE_API_URL), ссылка — только http(s)
  const img = mediaSrc(a.image)
  const href = externalUrl(a.url)
  // изображение публикации из снимка — по нажатию в просмотре на весь экран (масштаб, текст OCR рядом)
  const [open, setOpen] = useState(false)
  const items = adGallery({ image: a.image })
  return (
    <div className="sig-ad">
      {img ? (
        <button type="button" className="ph mv-open" onClick={() => setOpen(true)} aria-label={`Открыть изображение публикации ${adKey(a.id)} в просмотре`} title="Открыть в просмотре: масштаб, текст OCR">
          <SafeImg src={img} alt="" fallback={<span className="noimg"><PlatformIcon p={a.platform} size={36} /></span>} /><span className="mv-hint sm"><IcZoomIn />Открыть</span>
        </button>
      ) : <div className="ph"><span className="noimg"><PlatformIcon p={a.platform} size={36} /></span></div>}
      {open && items.length > 0 && (
        <MediaViewer items={items} title={`Изображение публикации ${adKey(a.id)}`} fileBase={adKey(a.id)}
          meta={{ platform: a.platform, account: a.account, date: a.date }} ocr={a.ocr} onClose={() => setOpen(false)} />
      )}
      <div style={{ minWidth: 0 }}>
        <div className="row between" style={{ gap: 8 }}>
          <div className="row" style={{ gap: 8, minWidth: 0 }}><PlatformIcon p={a.platform} size={20} /><span className="bold">{a.account}</span><span className="t-sm c2">{fmtDT(a.date)}</span></div>
          <StatusTag s={a.status} />
        </div>
        <p className="t-sm" style={{ margin: '6px 0 0', lineHeight: '19px' }}><Highlight text={a.text} /></p>
        {a.ocr && <p className="t-xs c2" style={{ marginTop: 4 }}>На изображении: <Highlight text={a.ocr} /></p>}
        <div className="row wrap-f" style={{ gap: 6, marginTop: 8 }}>
          <span className={`tag ${fact ? 'gray' : 'yellow'}`} title={fact ? 'Привязку к ЖК подтвердил аналитик — это факт' : 'Привязку предложил ИИ, аналитик её не подтвердил — это гипотеза'}>
            привязка — {fact ? `факт${a.linkConfirmedBy ? ` (подтвердил ${a.linkConfirmedBy})` : ''}` : `гипотеза ИИ ${Math.round(a.matchConfidence * 100)}%, в балл не входит`}
          </span>
          {a.price && <span className="tag gray">{a.price}</span>}
          <Link to={`/ads/${a.id}`} state={from(back.label, back.to)} className="tag outline" title="Открыть разбор публикации">{adKey(a.id)}</Link>
          {href && <a href={href} target="_blank" rel="noreferrer" className="tag outline"><IcExternal size={13} />Источник</a>}
        </div>
      </div>
    </div>
  )
}
