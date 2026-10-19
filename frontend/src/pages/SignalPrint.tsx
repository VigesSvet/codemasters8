import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Quote } from '../components/Factors'
import { SignalGate, SignalStatusTag, SignalTime } from '../components/SignalPanel'
import { platformMeta, statusMeta, zoneName } from '../components/ui'
import type { EvidenceItem, FactorState, ObservationState, Platform, ReadinessValue, Signal, SignalAd } from '../data/types'
import { basisText, law, lawRefs } from '../lib/law'
import { DEMO_ORG, isDemo } from '../lib/provenance'
import { fmtDate as day, linkConfirmed } from '../lib/risk'
import { IcChevronLeft } from '../ui/icons'

const STATE: Record<FactorState, string> = { hit: 'Подтверждён', unknown: 'Нужна проверка', clear: 'Не выявлен' }
const OBS: Record<ObservationState, string> = { flag: 'Есть замечание', unknown: 'Нет сведений', ok: 'Без замечаний' }
const DOC: Record<string, string> = { yes: 'есть', no: 'нет', unknown: 'нет данных' }
const APPEAL: Record<string, string> = { verified: 'проверено', pending: 'на проверке', rejected: 'отклонено' }
const join = (...xs: (string | number | null | undefined | false)[]) => xs.filter((x) => x || x === 0).join(' · ')
const plat = (p?: string) => (p && p in platformMeta ? platformMeta[p as Platform].name : p)
/** Привязка к ЖК: факт — только после подтверждения аналитиком, иначе гипотеза ИИ */
const linkLabel = (a: SignalAd) => linkConfirmed(a)
  ? `привязка — факт${a.linkConfirmedBy ? ` (подтвердил ${a.linkConfirmedBy})` : ''}`
  : `привязка — гипотеза ИИ ${Math.round(a.matchConfidence * 100)}%, в балл не входит`

/** Доказательство одной строкой — как в PDF */
function Item({ x }: { x: EvidenceItem }) {
  if (x.kind === 'ad') return <>{x.quote ? <Quote text={x.quote} marker={x.marker} /> : 'Публикация'} — {join(x.quote && x.quoteFrom === 'ocr' && 'из скриншота (OCR)', plat(x.source), x.label, x.date && day(x.date))}</>
  if (x.kind === 'document') return <><b>{x.label}</b>: {DOC[String(x.value)] ?? x.value ?? '—'} — {join(x.quote && `реквизит: ${x.quote}`, x.source || x.date ? join(x.source, x.date && `сверено ${day(x.date)}`, x.url) : 'не проверялся: источника и даты сверки нет')}</>
  if (x.kind === 'appeal') return <>Обращение: {x.label} — {join(APPEAL[String(x.value)] ?? x.value, x.source, x.date && day(x.date))}</>
  if (x.kind === 'readiness') return <>{x.label}: {x.value}% — {join(x.source, x.date && day(x.date))}</>
  if (x.kind === 'photo') return <>Снимок {join(x.date && day(x.date), x.source, x.value !== undefined && `активность: ${x.value}`)}</>
  return <>{x.label}{x.date && `: ${day(x.date)}`}</>
}

/** Нормативная база листа — как в PDF: статьи, на которые ссылаются факторы, адрес в ИПС «Әділет», оговорка и нюанс ТЗ */
function LawBase({ codes }: { codes: string[] }) {
  const used = new Set(codes.flatMap((c) => lawRefs(c).map((r) => r.article)))
  const arts = law.articles.filter((a) => used.has(a.num))
  if (!arts.length) return null
  return (
    <>
      <h3>Нормативная база</h3>
      <p className="sheet-small">
        {law.act.title} от 7 апреля 2016 года № {law.act.number} ({law.act.redaction}). Ссылки на нормы — справочные: оценка риска — не
        юридическое заключение. {law.nuance}
      </p>
      <ul>{arts.map((a) => <li key={a.num}><b>Ст. {a.num}.</b> {a.title} — <span className="sheet-url">{a.url}</span></li>)}</ul>
    </>
  )
}

// Формат листа — только пока открыта печатная версия: глобальный @page сломал бы выбор формата на других страницах
const PAGE = '@page { size: A4; margin: 14mm; }'

/** Печатная версия сигнала (T-15): A4, тот же состав, что PDF, без интерактива, читается в ч/б */
export default function SignalPrint() {
  const { id } = useParams()
  return (
    <div className="sigprint">
      <style>{PAGE}</style>
      <SignalGate id={id}>{(sig, { loadedAt }) => <Sheet sig={sig} at={loadedAt} />}</SignalGate>
    </div>
  )
}

function Sheet({ sig, at }: { sig: Signal; at: string }) {
  useEffect(() => {
    const prev = document.title
    document.title = `Сигнальная карточка ${sig.code}`
    return () => { document.title = prev }
  }, [sig.code])
  const ev = sig.evidence, c = ev.complex, dev = ev.developer, rd = c.readiness
  const unknown = sig.factors.filter((f) => f.state === 'unknown')
  // как в PDF: значение с источником и датой; без них — «без источника»
  const sourced = (v: ReadinessValue) => { const tail = join(v.source, v.date && day(v.date)); return tail ? `${v.value}% — ${tail}` : `${v.value}%, без источника` }

  return (
    <>
      <div className="sigprint-bar no-print">
        <Link to={`/signals/${sig.id}`} className="btn g m"><IcChevronLeft size={18} />Назад к сигналу</Link>
        <span className="t-sm c2">A4, книжная, поля 14 мм</span>
        <button className="btn y m" onClick={() => window.print()}>Печать</button>
      </div>

      <article className="sheet">
        <header className="sheet-head">
          <div>
            <div className="sheet-org">SAQ Qurylys · ДЭР ВКО · Усть-Каменогорск</div>
            <h1>Сигнальная карточка {sig.code}</h1>
            <div className="sheet-sub">ЖК «{c.name}» · {c.address}{isDemo(c) && ' · демо-сценарий (вымышленный ЖК)'}</div>
          </div>
          <dl className="sheet-meta">
            <dt>Сформирована</dt><dd><SignalTime at={at} numeric /></dd>
            <dt>Сигнал создан</dt><dd><SignalTime at={sig.createdAt} numeric />, {sig.createdBy}</dd>
            <dt>Модель</dt><dd>{sig.modelVersion}</dd>
          </dl>
        </header>
        <div className="sheet-note">{isDemo(c) && 'Демонстрационные данные. '}Рекомендация для проверки, не юридическое заключение. Автоматической отправки в госорганы нет.</div>

        <section>
          <h2>1. Объект</h2>
          <table className="sheet-kv"><tbody>
            <tr><th>Название</th><td>ЖК «{c.name}»</td></tr>
            <tr><th>Адрес</th><td>{join(c.address, c.district && `р-н ${c.district}`)}</td></tr>
            <tr><th>Застройщик</th><td>{dev ? join(dev.name, `БИН ${dev.bin}`, isDemo(dev) && DEMO_ORG) : 'нет данных'}</td></tr>
            <tr><th>Стадия и готовность</th><td>
              {rd?.confirmed ? `${c.stage}, подтверждённая готовность ${sourced(rd.confirmed)}` : `${c.stage}; готовность по карточке объекта ${c.progress}%, без источника`}
              <br />Заявлено: {rd?.declared ? sourced(rd.declared) : 'нет данных'}
              {!rd?.confirmed && <><br />Подтверждено: нет данных</>}
            </td></tr>
            <tr><th>Срок сдачи</th><td>{join(c.plannedEnd ? `заявлен ${day(c.plannedEnd)}` : 'не указан', c.start && `начало ${day(c.start)}`)}</td></tr>
            <tr><th>Основание привлечения средств</th><td>{ev.documents.length ? ev.documents.map((d, i) => <div key={i}><Item x={d} /></div>) : 'нет данных'}</td></tr>
          </tbody></table>
        </section>

        <section className="keep">
          <h2>2. Оценка риска</h2>
          <div className="sheet-risk">
            <div className="sheet-score"><b>{sig.score}</b> из 100<div className="sheet-zone">{zoneName[sig.zone]}</div></div>
            <div>
              <p>{sig.summary}</p>
              <p><b>Нужна проверка:</b> {unknown.length ? unknown.map((f) => `${f.code} — ${f.title}`).join('; ') : 'нет'}</p>
            </div>
          </div>
        </section>

        <section>
          <h2>3. Факторы риска R1–R6 (ТЗ §5)</h2>
          <div className="pf head"><span>Код</span><span>Фактор и основание</span><span>Статус</span><span>Баллы</span></div>
          {sig.factors.map((f) => (
            <div key={f.code} className={`pf ${f.state}`}>
              <b>{f.code}</b>
              <div>
                <b>{f.title}</b>
                <div className="pf-e">{f.evidence}</div>
                {basisText(f.code) && <div className="pf-e">{basisText(f.code)}</div>}
                {f.items.length > 0 && <ul>{f.items.map((x, i) => <li key={i}><Item x={x} /></li>)}</ul>}
              </div>
              <span>{STATE[f.state]}</span>
              <span className="num">{f.state === 'hit' ? f.points : 0} из {f.points}</span>
            </div>
          ))}
          <div className="pf total"><span /><b>Итого — сумма весов подтверждённых факторов, не больше 100</b><span /><b className="num">{sig.score}</b></div>
          <LawBase codes={sig.factors.map((f) => f.code)} />
        </section>

        {sig.observations.length > 0 && (
          <section className="keep">
            <h2>4. Наблюдения вне балла</h2>
            <p className="sheet-small">Не влияют на балл и зону — контекст для аналитика.</p>
            <table className="sheet-grid">
              <thead><tr><th>Код</th><th>Наблюдение</th><th>Состояние</th><th>Пояснение</th></tr></thead>
              <tbody>{sig.observations.map((o) => <tr key={o.code}><td>{o.code}</td><td>{o.title}</td><td className="nowrap">{OBS[o.state]}</td><td>{o.text}</td></tr>)}</tbody>
            </table>
          </section>
        )}

        <section>
          <h2>5. Материалы (снимок на момент создания сигнала)</h2>
          <h3>Публикации</h3>
          {ev.ads.length ? (
            <ul>
              {ev.ads.map((a) => (
                <li key={a.id}>
                  <b>{join(platformMeta[a.platform]?.name ?? a.platform, a.account, day(a.date), statusMeta[a.status]?.name.toLowerCase(), linkLabel(a))}</b>
                  <div>{a.text}</div>
                  {a.url && <div className="sheet-url">{a.url}</div>}
                </li>
              ))}
            </ul>
          ) : <p className="sheet-small">Привязанных публикаций нет</p>}
          <h3>Обращения граждан</h3>
          {ev.appeals.length ? <ul>{ev.appeals.map((a) => <li key={a.id}>{join(day(a.date), a.subject, `заявителей: ${a.applicants}`, APPEAL[a.status], a.source)}</li>)}</ul> : <p className="sheet-small">Обращений нет</p>}
          <h3>Снимки стройки</h3>
          {ev.photos.length ? <ul>{ev.photos.map((p, i) => <li key={i}>{join(day(p.date), p.source, `активность: ${p.activity}`, p.note)}</li>)}</ul> : <p className="sheet-small">Снимков нет</p>}
        </section>

        <section className="keep">
          <h2>6. Статус проверки</h2>
          <table className="sheet-kv"><tbody>
            <tr><th>Статус</th><td><SignalStatusTag s={sig.status} /></td></tr>
            <tr><th>Создан</th><td>{sig.createdBy}, <SignalTime at={sig.createdAt} numeric /></td></tr>
            <tr><th>Решение</th><td>{sig.decidedAt ? <>{sig.decidedBy}, <SignalTime at={sig.decidedAt} numeric /></> : 'ещё не принято'}</td></tr>
            <tr><th>Комментарий</th><td>{sig.decisionComment || '—'}</td></tr>
            <tr><th>Выгрузка PDF</th><td>{sig.exportedAt ? <>{sig.fileName}, <SignalTime at={sig.exportedAt} numeric /></> : 'не выгружался'}</td></tr>
          </tbody></table>
        </section>

        <section>
          <h2>7. Журнал</h2>
          <table className="sheet-grid">
            <thead><tr><th>Когда</th><th>Кто</th><th>Событие</th></tr></thead>
            <tbody>{sig.journal.map((e, i) => <tr key={i}><td className="nowrap"><SignalTime at={e.at} numeric /></td><td className="nowrap">{e.who}</td><td>{e.what}</td></tr>)}</tbody>
          </table>
        </section>

        {sig.status === 'new' ? (
          // решения ещё нет — поля для отметки на бумаге
          <section className="sheet-sign keep">
            <h2>Отметка аналитика</h2>
            <div className="sign-choice"><span><i />Подтвердить сигнал</span><span><i />Вернуть на доработку</span></div>
            <div className="sign-line wide"><span>Основание / комментарий</span><i /></div>
            <div className="sign-line wide"><span /><i /></div>
            <div className="sign-row">
              <div className="sign-line"><span>Аналитик (ФИО)</span><i /></div>
              <div className="sign-line"><span>Подпись</span><i /></div>
              <div className="sign-line"><span>Дата</span><i /></div>
            </div>
          </section>
        ) : (
          // решение уже в журнале — итог вместо пустых полей; подпись — под принятым решением
          <section className="sheet-sign keep">
            <h2>Решение аналитика</h2>
            <table className="sheet-kv"><tbody>
              <tr><th>Итог</th><td><b>{sig.status === 'confirmed' ? 'Сигнал подтверждён' : 'Возвращён на доработку'}</b></td></tr>
              <tr><th>Кто и когда</th><td>{sig.decidedBy ?? '—'}{sig.decidedAt && <>, <SignalTime at={sig.decidedAt} numeric /></>}</td></tr>
              <tr><th>{sig.status === 'confirmed' ? 'Основание' : 'Что доработать'}</th><td>{sig.decisionComment || '—'}</td></tr>
            </tbody></table>
            <div className="sign-row">
              <div className="sign-line"><span>Подпись</span><i /></div>
              <div className="sign-line"><span>Дата</span><i /></div>
            </div>
          </section>
        )}

        <footer className="sheet-foot">SAQ Qurylys · {sig.code} · Рекомендация для проверки, не юридическое заключение. Решение принимает сотрудник.</footer>
      </article>
    </>
  )
}
