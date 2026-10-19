import { coverSrc } from '../lib/gallery'
import { useEffect, useState } from 'react'
import { flushSync } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { Faq, Hero, RiskScore, RiskZone, Seg, Toggle, adKey, fmtDate, platformMeta, statusMeta, zoneName } from '../components/ui'
import type { Ad, FactorState, Zone } from '../data/types'
import { isDemo, progressText, stageText } from '../lib/provenance'
import { NOT_ASSESSED, adSignals, isAssessed, linkConfirmed, model } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcDoc } from '../ui/icons'
import { Board, Kpi } from '../components/StatBoard'

// Всё на странице — из данных: публикации по дате, риск, сигналы (создание и решение). Выдуманных рядов и счётчиков нет (ТЗ §4)
type Period = 'w' | 'm' | 'q'
const N: Record<Period, number> = { w: 8, m: 4, q: 3 }
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
const NAME: Record<Period, string> = { w: 'неделя', m: 'месяц', q: 'квартал' }
const BY: Record<Period, string> = { w: 'неделям', m: 'месяцам', q: 'кварталам' }
const PREV: Record<Period, string> = { w: 'прошлая неделя', m: 'прошлый месяц', q: 'прошлый квартал' }
const ZONES: Zone[] = ['red', 'yellow', 'green']
const STATE: Record<FactorState, string> = { hit: 'подтверждён', unknown: 'нужна проверка', clear: 'не выявлен' }
// Лист A4 — только пока открыт отчёт: глобальный @page сломал бы печать других страниц (как у печатной версии сигнала)
const PAGE = '@page { size: A4; margin: 14mm; }'

interface Bucket { from: Date; to: Date; label: string }
const isoWeek = (d: Date) => {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7))
  return Math.ceil(((t.getTime() - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7)
}
const startOf = (p: Period, d: Date) => p === 'w' ? new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() + 6) % 7))
  : new Date(d.getFullYear(), p === 'm' ? d.getMonth() : d.getMonth() - (d.getMonth() % 3), 1)
const shift = (p: Period, d: Date, n: number) => p === 'w' ? new Date(d.getFullYear(), d.getMonth(), d.getDate() + 7 * n)
  : new Date(d.getFullYear(), d.getMonth() + (p === 'm' ? n : 3 * n), 1)
const label = (p: Period, d: Date) => p === 'w' ? `н${isoWeek(d)}` : p === 'm' ? MONTHS[d.getMonth()] : `${['I', 'II', 'III', 'IV'][Math.floor(d.getMonth() / 3)]} кв`
/** Последние N недель / месяцев / кварталов; последний — текущий, по сегодня */
const buckets = (p: Period, now: Date): Bucket[] => {
  const cur = startOf(p, now)
  return Array.from({ length: N[p] }, (_, i) => { const from = shift(p, cur, i - N[p] + 1); return { from, to: shift(p, from, 1), label: label(p, from) } })
}
const inB = (iso: string | null | undefined, b: Bucket) => { const t = iso ? new Date(iso).getTime() : NaN; return t >= b.from.getTime() && t < b.to.getTime() }
const dm = (d: Date) => d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
const full = (d: Date) => `${dm(d)} ${d.getFullYear()}`
const range = (a: Date, b: Date) => a.toDateString() === b.toDateString() ? full(b)
  : a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear() ? `${a.getDate()}–${full(b)}`
    : a.getFullYear() === b.getFullYear() ? `${dm(a)} — ${full(b)}` : `${full(a)} — ${full(b)}`
const dur = (ms: number) => { const h = ms / 36e5; return h < 1 ? `${Math.max(1, Math.round(ms / 6e4))} мин` : h < 48 ? `${Math.round(h)} ч` : `${(h / 24).toFixed(1).replace('.', ',')} дн.` }
/** Признаки продажи в тексте или на скриншоте: маркеры словарей R3 и R2; отклонённые аналитиком публикации не считаем */
const saleMarkers = (a: Ad) => { const s = adSignals(a); return a.status === 'dismissed' ? [] : [...new Set([...s.funds, ...s.sale])] }

/** Отчёты — шапка с конструктором отчёта (как ипотечный калькулятор), ниже рейтинг и FAQ; печать — лист A4 с выбранными разделами */
export default function Reports() {
  const nav = useNavigate()
  const { mode, snapshot, complexes, ads, developers, signals, user, riskOf } = useStore()
  const live = mode === 'api'
  const [period, setPeriod] = useState<Period>('w')
  const [parts, setParts] = useState({ map: true, rating: true, ads: true, photos: false, method: true })
  // время формирования: при открытии и заново перед печатью (кнопка, Ctrl+P) — на листе время печати
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const on = () => flushSync(() => setNow(new Date()))
    window.addEventListener('beforeprint', on)
    return () => window.removeEventListener('beforeprint', on)
  }, [])
  // не оценённые (карточки реестра без проверенных сведений) — после оценённых и ни в одной зоне
  const scored = complexes.map((c) => ({ c, r: riskOf(c) }))
    .sort((a, b) => Number(isAssessed(b.r)) - Number(isAssessed(a.r)) || b.r.score - a.r.score)
  const unscored = scored.filter((x) => !isAssessed(x.r))
  const confirmed = ads.filter((a) => a.status === 'confirmed').length
  const red = scored.filter((x) => isAssessed(x.r) && x.r.zone === 'red').length
  const bs = buckets(period, now)
  const cur = bs[bs.length - 1]
  const periodText = range(cur.from, now)
  const series = bs.map((b) => ads.filter((a) => inB(a.date, b) && saleMarkers(a).length > 0).length)
  const max = Math.max(1, ...series)
  // текущий период ещё идёт — без процента роста к прошлому: рядом просто число за прошлый
  const [last, prev] = [series[series.length - 1], series[series.length - 2]]
  const periodAds = ads.filter((a) => inB(a.date, cur)).sort((a, b) => b.date.localeCompare(a.date))
  // сигналы — с сервера (в офлайн-копии — её реестр); время решения — от создания до решения по журналу сигнала (decidedAt)
  const decided = signals.filter((s) => s.status !== 'new' && s.decidedAt)
  const avg = decided.length ? decided.reduce((x, s) => x + Date.parse(s.decidedAt!) - Date.parse(s.createdAt), 0) / decided.length : null
  const sigPeriod = signals.filter((s) => inB(s.createdAt, cur)).length
  const sigNew = signals.filter((s) => s.status === 'new').length
  const noSig = mode === 'loading' ? 'загружаем…' : undefined
  const chosen = Object.values(parts).filter(Boolean).length
  const part = (k: keyof typeof parts, title: string, sub: string) => (
    <div className="crow"><span>{title}<span className="t-sm c2" style={{ display: 'block' }}>{sub}</span></span><Toggle on={parts[k]} onChange={(v) => setParts((p) => ({ ...p, [k]: v }))} /></div>
  )
  const summary: [string, string | number][] = [
    [`Публикаций за период (${NAME[period]})`, periodAds.length],
    ['Из них с признаками продажи', last],
    ['Подтверждено нарушений по публикациям, всего', confirmed],
    ['Объектов в красной зоне сейчас', red],
    ['Сигналов за период', noSig ? '—' : sigPeriod],
    ['Сигналы ждут решения', noSig ? '—' : sigNew],
    ['Среднее время решения по сигналу', avg === null ? '—' : `${dur(avg)} (решений: ${decided.length})`],
  ]
  const building = scored.filter((x) => x.c.stage !== model.builtStage).sort((a, b) => b.c.idleDays - a.c.idleDays)
  const demoData = complexes.some(isDemo)

  return (
    <>
      <style>{PAGE}</style>
      <div className="rscreen">
        <Hero crumb="Отчёты" title="Отчёт для руководства" info={`Период: ${periodText} (${NAME[period]} по сегодня) · Усть-Каменогорск · всё по текущим данным`}
          board={<Board kind="reports">
            <Kpi em="feed" value={periodAds.length} label="Публикаций за период" to="/ads?sort=date" />
            <Kpi em="stamp" tone="red" value={confirmed} label="Подтверждено нарушений" hint="по публикациям, всего" to="/ads?status=confirmed" />
            <Kpi em="traffic" tone="red" value={red} label="Объектов в красной зоне" to="/complexes?zone=red" />
            <Kpi em="timer" value={avg === null ? '—' : dur(avg)} label="Среднее время решения" hint={noSig ?? (decided.length ? `по сигналу · решений: ${decided.length}` : 'решённых сигналов нет')} to="/signals" />
            <Kpi em="flag" value={noSig ? '—' : sigPeriod} label="Сигналов за период" to="/signals" />
            <Kpi em="flag" tone="yellow" value={noSig ? '—' : sigNew} label="Сигналы ждут решения" to="/signals?status=new" />
          </Board>}>
          <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 380px' }}>
            <div className="pl">
              <div className="row between" style={{ marginBottom: 8 }}><h2 className="t-h2">Соберите отчёт</h2><Seg value={period} onChange={setPeriod} items={[['w', 'Неделя'], ['m', 'Месяц'], ['q', 'Квартал']]} /></div>
              {part('map', 'Светофор', 'Сколько объектов в каждой зоне риска и какие')}
              {part('rating', 'Рейтинг риска', 'Объекты по убыванию балла с факторами')}
              {part('ads', 'Реклама за период', 'Публикации периода: признаки продажи, привязка и решения')}
              {part('photos', 'Фото-мониторинг', 'Простой площадок по снимкам')}
              {part('method', 'Методика', 'Как считается балл — для приложения')}
            </div>
            <div className="pr">
              <span className="c2 t-sm">Публикации с признаками продажи · по {BY[period]}</span>
              <div className="row" style={{ alignItems: 'flex-end', gap: 8, margin: '8px 0 16px' }}>
                <span className="big-num">{last}</span>
                <span className="t-sm c2" style={{ marginBottom: 4 }}>{NAME[period] === 'неделя' ? 'текущая' : 'текущий'} {NAME[period]}, по сегодня · {PREV[period]}: {prev}</span>
              </div>
              <div className="bars" style={{ height: 130 }}>{series.map((v, i) => <div key={i} title={`${bs[i].label}: ${v}`}><b style={{ height: Math.max(2, (v / max) * 100), background: i === series.length - 1 ? 'var(--yellow)' : '#fff' }} /><span className="t-xs c3">{bs[i].label}</span></div>)}</div>
              <span className="t-xs c2" style={{ marginTop: 8 }}>По дате публикации, маркеры словарей R2 и R3; отклонённые аналитиком не считаются</span>
              <button className="btn y full" style={{ marginTop: 16 }} disabled={!chosen} onClick={() => window.print()}><IcDoc />Печать / сохранить в PDF</button>
              <span className="t-xs c2" style={{ marginTop: 6 }}>{chosen ? 'Окно печати браузера: принтер или «Сохранить как PDF». Лист A4, выбранные разделы' : 'Включите хотя бы один раздел'}</span>
            </div>
          </div>
        </Hero>

        <h2 className="t-h2" style={{ marginBottom: 4 }}>Рейтинг риска</h2>
        <p className="t-sm c2">В колонке «Факторы» серым — подтверждённые, жёлтым — нужна проверка (0 баллов, пока не проверено)</p>
        <table className="ytable">
          <thead><tr><th>#</th><th>Жилой комплекс</th><th>Застройщик</th><th>Факторы</th><th className="r">Балл</th></tr></thead>
          <tbody>
            {scored.map(({ c, r }, i) => (
              <tr key={c.id} onClick={() => nav(`/complexes/${c.id}`)}>
                <td className="c3 num">{i + 1}</td>
                <td><div className="row" style={{ gap: 12 }}><img src={coverSrc(c)} alt="" style={{ width: 56, height: 42, borderRadius: 8, objectFit: 'cover' }} /><div><span className="link">ЖК «{c.name}»</span><div className="t-sm c2">{[c.district, stageText(c)].filter(Boolean).join(' · ')}</div></div></div></td>
                <td className="t-sm c2">{developers.find((d) => d.id === c.developerId)?.name}</td>
                <td><div className="row wrap-f" style={{ gap: 4 }}>
                  {r.factors.filter((f) => f.state !== 'clear').map((f) => <span key={f.code} className={`tag ${f.state === 'hit' ? 'gray' : 'yellow'}`} title={f.evidence}>{f.code}{f.state === 'unknown' && '?'}</span>)}
                  {r.factors.every((f) => f.state === 'clear') && <span className="c3">—</span>}
                </div></td>
                <td className="r"><div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}><RiskZone r={r} /><RiskScore r={r} /></div></td>
              </tr>
            ))}
          </tbody>
        </table>

        <Faq title="Вопросы об отчёте"
          items={[
            ['Как сохранить отчёт в PDF?', 'Кнопка «Печать / сохранить в PDF» открывает окно печати браузера: выберите принтер или «Сохранить как PDF». На листе A4 — шапка с периодом, автором и временем, сводка и разделы, включённые в конструкторе.'],
            ['Кому отправлять отчёт?', 'Руководителю департамента и в ГАСК. Отчёт — внутренний документ: в нём рекомендации системы и решения аналитиков, автоматически в госорганы он не уходит.'],
            ['Можно ли использовать отчёт как доказательство нарушения?', 'Нет. Отчёт показывает приоритеты для проверки. Нарушение подтверждает сотрудник по документам: картотеке ГАСК, гарантиям ЕО, разрешениям МИО.'],
            ['Почему цифры за неделю и месяц не совпадают с лентой?', 'В период попадают публикации, опубликованные с начала текущей недели, месяца или квартала по сегодня. Лента показывает всё, что есть в базе сейчас. Зоны и рейтинг — на сегодня.'],
          ]} />
      </div>

      {/* Печатный лист: на экране скрыт, при печати — вместо страницы (стили — provenance.css, «Печать отчёта») */}
      <article className="sheet rprint">
        <header className="sheet-head">
          <div>
            <div className="sheet-org">SAQ Qurylys · ДЭР ВКО · Усть-Каменогорск</div>
            <h1>Отчёт для руководства</h1>
            <div className="sheet-sub">Период: {periodText} ({NAME[period]} по день формирования)</div>
          </div>
          <dl className="sheet-meta">
            <dt>Сформирован</dt><dd>{now.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })}</dd>
            <dt>Подготовил</dt><dd>{user?.name ?? '—'}</dd>
            <dt>Модель</dt><dd>{model.version}</dd>
            <dt>Данные</dt><dd>{live ? 'сервер' : mode === 'snapshot' ? `офлайн-копия стартового набора${snapshot?.asOf ? ` на ${snapshot.asOf.split('-').reverse().join('.')}` : ''}` : 'загружаются'}</dd>
          </dl>
        </header>
        <div className="sheet-note">{demoData && 'Демонстрационные данные: ЖК и организации вымышлены. '}Рекомендация для проверки, не юридическое заключение. Автоматической отправки в госорганы нет.</div>

        <section className="keep">
          <h2>Сводка</h2>
          <table className="sheet-kv"><tbody>{summary.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></table>
          {noSig && <p className="sheet-small">Сигналы: {noSig}.</p>}
        </section>

        {parts.map && (
          <section>
            <h2>Светофор: объекты по зонам</h2>
            <table className="sheet-grid">
              <thead><tr><th>Зона</th><th>Объектов</th><th>Объекты</th></tr></thead>
              <tbody>{ZONES.map((z) => { const xs = scored.filter((x) => isAssessed(x.r) && x.r.zone === z); return (
                <tr key={z}><td className="nowrap">{zoneName[z]}</td><td>{xs.length}</td><td>{xs.map((x) => `${x.c.name} (${x.r.score})`).join(', ') || '—'}</td></tr>
              ) })}
              {unscored.length > 0 && <tr><td className="nowrap">Риск не оценивается</td><td>{unscored.length}</td><td>{unscored.map((x) => x.c.name).join(', ')} — {NOT_ASSESSED.toLowerCase()}</td></tr>}</tbody>
            </table>
            <p className="sheet-small">Нужна проверка (часть сведений не подтверждена, 0 баллов за них): {scored.filter((x) => x.r.needsCheck).length} из {scored.length}.</p>
          </section>
        )}

        {parts.rating && (
          <section>
            <h2>Рейтинг риска</h2>
            <table className="sheet-grid">
              <thead><tr><th>#</th><th>Жилой комплекс</th><th>Застройщик</th><th>Подтверждено</th><th>Нужна проверка</th><th>Балл</th><th>Зона</th></tr></thead>
              <tbody>{scored.map(({ c, r }, i) => (
                <tr key={c.id}>
                  <td>{i + 1}</td><td>ЖК «{c.name}»<div className="sheet-small">{[c.district, stageText(c)].filter(Boolean).join(' · ')}</div></td>
                  <td>{developers.find((d) => d.id === c.developerId)?.name ?? '—'}</td>
                  <td>{r.factors.filter((f) => f.state === 'hit').map((f) => f.code).join(', ') || '—'}</td>
                  <td>{r.factors.filter((f) => f.state === 'unknown').map((f) => f.code).join(', ') || '—'}</td>
                  {isAssessed(r) ? <><td><b>{r.score}</b></td><td className="nowrap">{zoneName[r.zone]}</td></> : <><td>—</td><td className="nowrap">не оценивается</td></>}
                </tr>
              ))}</tbody>
            </table>
          </section>
        )}

        {parts.ads && (
          <section>
            <h2>Реклама за период</h2>
            <p>Публикаций: {periodAds.length}, с признаками продажи: {last}.</p>
            {periodAds.length > 0 && (
              <table className="sheet-grid">
                <thead><tr><th>Публикация</th><th>Дата</th><th>Площадка, аккаунт</th><th>ЖК и привязка</th><th>Маркеры словаря</th><th>Статус</th></tr></thead>
                <tbody>{periodAds.map((a) => { const c = complexes.find((x) => x.id === a.complexId); const m = saleMarkers(a); return (
                  <tr key={a.id}>
                    <td className="nowrap">{adKey(a.id)}</td><td className="nowrap">{fmtDate(a.date)}</td>
                    <td>{platformMeta[a.platform]?.name ?? a.platform} · {a.account}</td>
                    <td>{c ? <>ЖК «{c.name}» — {linkConfirmed(a) ? 'факт' : `гипотеза ИИ ${Math.round((a.matchConfidence || 0) * 100)}%`}</> : 'не привязана'}</td>
                    <td>{m.join(', ') || '—'}</td><td>{statusMeta[a.status].name}</td>
                  </tr>
                ) })}</tbody>
              </table>
            )}
          </section>
        )}

        {parts.photos && (
          <section>
            <h2>Фото-мониторинг</h2>
            <table className="sheet-grid">
              <thead><tr><th>Жилой комплекс</th><th>Стадия</th><th>Снимков</th><th>Последний снимок</th><th>Дней без изменений</th><th>R6</th></tr></thead>
              <tbody>{building.map(({ c, r }) => { const lastPh = c.photos.at(-1); const r6 = r.factors.find((f) => f.code === 'R6'); return (
                <tr key={c.id}>
                  <td>ЖК «{c.name}»</td><td>{progressText(c)}</td><td>{c.photos.length}</td>
                  <td>{lastPh ? `${fmtDate(lastPh.date)}, ${lastPh.source}` : 'нет'}</td><td>{c.idleDays}</td><td>{r6 ? STATE[r6.state] : '—'}</td>
                </tr>
              ) })}</tbody>
            </table>
          </section>
        )}

        {parts.method && (
          <section>
            <h2>Методика (модель {model.version})</h2>
            <p>{model.disclaimer}</p>
            <table className="sheet-grid">
              <thead><tr><th>Код</th><th>Фактор</th><th>Вес</th></tr></thead>
              <tbody>{model.factors.map((f) => <tr key={f.code}><td>{f.code}</td><td>{f.title}</td><td>{f.weight}</td></tr>)}</tbody>
            </table>
            <ul>{model.zones.items.map((z) => <li key={z.zone}><b>{z.name} зона</b>: {z.rule}</li>)}</ul>
          </section>
        )}

        <footer className="sheet-foot">SAQ Qurylys · отчёт за {periodText} · Рекомендация для проверки, не юридическое заключение. Решение принимает сотрудник.</footer>
      </article>
    </>
  )
}
