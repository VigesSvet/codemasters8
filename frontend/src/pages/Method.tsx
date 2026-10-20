import { useEffect, useMemo, useState, type ComponentType } from 'react'
import { Link } from 'react-router-dom'
import { AiBadge } from '../components/Hypothesis'
import { Faq, Gauge, RiskScore, Seg, ZoneTag, zoneShort } from '../components/ui'
import type { AiEval, FactorState } from '../data/types'
import { errorText } from '../lib/api'
import { excerptsFor, factorsOf, law, lawArticle, lawRefs, refText, refWhy } from '../lib/law'
import { from } from '../lib/nav'
import { isDemo } from '../lib/provenance'
import { factorDef, fmtDate, isAssessed, model, zoneFor, type ModelCase } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBank, IcCheck, IcChevronDown, IcChevronUp, IcClock, IcClose, IcHelp, IcInfo, IcMegaphone, IcUsersFamily, IcWallet, IcWorker } from '../ui/icons'
import { Board, Cap } from '../components/StatBoard'

const T = model.thresholds
const ICONS: Record<string, ComponentType<{ size?: number }>> = { R1: IcBank, R2: IcWallet, R3: IcMegaphone, R4: IcUsersFamily, R5: IcWorker, R6: IcClock }
// Подписи состояний в калькуляторе — по смыслу фактора: [подтверждён, нет, нет сведений]
const STATE_LABELS: Record<string, [string, string, string]> = {
  R1: ['Установлено отсутствие', 'Основание есть', 'Нет сведений'],
  R2: ['Есть в публикации', 'Нет', 'Привязка — гипотеза ИИ'],
  R3: ['Есть публикация', 'Нет', 'Привязка — гипотеза ИИ'],
  R4: ['Проверено', 'Нет', 'На проверке'],
  R5: [`Разница от ${T.readinessGap} п.п.`, 'Меньше порога', 'Нет пары значений'],
  R6: ['Подтверждён', 'Нет', 'Не подтверждён'],
}
const labels = (code: string) => STATE_LABELS[code] ?? ['Подтверждён', 'Нет', 'Нет сведений']

type Dict = keyof typeof model.markers
const DICTS: [Dict, string, string, string][] = [
  ['money', 'Предложение денег · R2', 'fund', 'Бронь, депозит, взнос, рассрочка, ДДУ. Маркер в тексте или на скриншоте привязанной публикации подтверждает R2, а заодно и R3.'],
  ['sale', 'Реклама · R3', '', 'Продажа и продвижение квартир до ввода объекта. Маркер в привязанной публикации подтверждает R3.'],
  ['safe', 'Признаки законности (только подсветка)', 'safe', 'Добросовестный застройщик обычно указывает основание. Эти слова подсвечиваются зелёным в тексте публикации и на балл не влияют.'],
  ['search', 'Стартовые слова ГАСК (для сбора)', '', 'По этим словам собираются публикации в ленту. На балл не влияют: оценивает только словарь R2 и R3.'],
]

const weight = (code: string) => factorDef(code)?.weight ?? 0
const CODES = model.factors.map((f) => f.code)

/** «ст. 7 п. 1, 2 · ст. 8 п. 6» — ссылки на статьи в ИПС «Әділет»; норма не относится — «—» */
function LawRefs({ code }: { code: string }) {
  const rs = lawRefs(code)
  if (!rs.length) return <span className="c3">—</span>
  return <>{rs.map((r, i) => {
    const a = lawArticle(r.article)
    return <span key={i}>{i > 0 && ' · '}{a ? <a className="link" href={a.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>{refText(r)}</a> : refText(r)}</span>
  })}</>
}

/** «Нормативная база»: статьи закона — пересказ, дословные выдержки по раскрытию, ссылка на «Әділет» и факторы, которые на них ссылаются */
function LawBlock() {
  const docs = model.documents.filter((d) => refWhy(d.key).length)
  return (
    <>
      <h2 className="t-h2" id="law" style={{ margin: '48px 0 4px' }}>Нормативная база</h2>
      <p className="c2" style={{ marginBottom: 12, maxWidth: 760 }}>
        <a className="link" href={law.act.url} target="_blank" rel="noopener noreferrer">{law.act.title}</a> от 7 апреля 2016 года № {law.act.number}.
        Ориентиры ТЗ — статьи 7, 8 и 18; статья 23 — о рекламе. Текст — {law.act.source}; {law.act.redaction.charAt(0).toLowerCase() + law.act.redaction.slice(1)}, получен {fmtDate(law.act.fetchedAt)}.
      </p>
      <div className="box warn t-sm" style={{ lineHeight: '20px', marginBottom: 16, maxWidth: 760 }}>
        <div className="bold" style={{ marginBottom: 4 }}>Оценка — не юридическое заключение</div>
        <div>{law.nuance} Поэтому R1 ставится, только когда проверкой установлено отсутствие и гарантии ЕО, и разрешения МИО, а при действующей гарантии ЕО отсутствие разрешения МИО не учитывается (ст. 8 п. 6).</div>
      </div>
      <div className="stack" style={{ gap: 12 }}>
        {law.articles.map((a) => {
          const codes = factorsOf(a.num, CODES)
          return (
            <div key={a.num} className="box t-sm" style={{ lineHeight: '20px' }}>
              <div className="row between" style={{ gap: 12, alignItems: 'flex-start' }}>
                <div className="bold">Статья {a.num}. {a.title}</div>
                <span className="row" style={{ gap: 6, flexShrink: 0 }}>{codes.map((c) => <a key={c} href={`#${c}`} className="tag dark">{c}</a>)}</span>
              </div>
              <div style={{ marginTop: 6 }}><span className="c2">Кратко (пересказ, не текст закона): </span>{a.summary}</div>
              <details style={{ marginTop: 8 }}>
                <summary className="link" style={{ cursor: 'pointer' }}>Текст статьи — выдержки ({a.excerpts.length})</summary>
                {a.excerpts.map((e) => <blockquote key={e.part} style={{ margin: '8px 0 0', paddingLeft: 12, borderLeft: '3px solid var(--border)', whiteSpace: 'pre-line' }}>{e.text}</blockquote>)}
                {a.footnote && <div className="t-xs c3" style={{ marginTop: 8 }}>{a.footnote}</div>}
              </details>
              <div style={{ marginTop: 8 }}><a className="link" href={a.url} target="_blank" rel="noopener noreferrer">Полный текст статьи в ИПС «Әділет»</a></div>
            </div>
          )
        })}
      </div>
      {docs.length > 0 && (
        <table className="ytable" style={{ marginTop: 16 }}>
          <thead><tr><th>Документ основания</th><th>Норма</th><th>Почему</th></tr></thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.key}><td>{d.label}</td><td className="nowrap"><LawRefs code={d.key} /></td><td className="c2">{refWhy(d.key).map((r) => r.why).join(' ')}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="t-sm c3" style={{ marginTop: 8, maxWidth: 760 }}>{law.note}</p>
    </>
  )
}
/** «R2 25 + R3 10 = 35» — расчёт по подтверждённым факторам */
const formula = (hits: string[]) => {
  const sum = Math.min(model.maxScore, hits.reduce((s, c) => s + weight(c), 0))
  return hits.length ? `${hits.map((c) => `${c} ${weight(c)}`).join(' + ')} = ${sum}` : '0 баллов'
}
const fromCase = (k: ModelCase): Record<string, FactorState> =>
  Object.fromEntries(model.factors.map((f) => [f.code, k.hits.includes(f.code) ? 'hit' : k.unknown.includes(f.code) ? 'unknown' : 'clear']))

const pct = (x: number) => `${Math.round(x * 100)}%`
const TERM: Record<string, string> = {
  booking: 'бронь', deposit: 'депозит', down_payment: 'взнос', installment: 'рассрочка', ddu: 'ДДУ', direct_payment: 'оплата застройщику',
  price: 'цена', investment: 'инвестиции',
}
const AI_TILES: [string, string][] = [
  ['OCR скриншота', 'Tesseract (нейросеть LSTM) — русский, казахский, английский. Перед распознаванием: оттенки серого, увеличение мелких изображений, автоконтраст. Распознанный текст аналитик может поправить; цитаты из него помечаются «из скриншота (OCR)».'],
  ['Условия оплаты', 'Платная бронь, депозит, первый взнос, рассрочка, ДДУ, оплата напрямую застройщику, цена за м², «инвестиции». Суммы в ₸, $ и ₽ в любой записи («500 тыс. тг», «1,2 млн ₸»), проценты и сроки. У каждого условия — дословная цитата и уверенность.'],
  ['Сопоставление с ЖК', 'Название с транслитерацией KZ/RU/Latin и опечатками, адрес (улица и дом), аккаунт и телефон застройщика, прежние подтверждённые публикации аккаунта, район. До трёх гипотез с причинами; подборка нескольких ЖК снижает уверенность каждой.'],
]

type EvalTerm = { kind?: string; amount?: number | null; currency?: string | null; unit?: string | null; percent?: number | null; months?: number | null; money?: boolean }
type EvalSide = {
  complexId?: string | null; complexName?: string | null; confidence?: number | null; low?: boolean | null; noHypotheses?: boolean; terms?: EvalTerm[]
}
const CUR: Record<string, string> = { KZT: '₸', USD: '$', RUB: '₽' }
const termShort = (t: EvalTerm) => [
  TERM[t.kind ?? ''] ?? t.kind,
  t.amount != null && `${t.amount.toLocaleString('ru-RU')} ${CUR[t.currency ?? 'KZT'] ?? t.currency}${t.unit === 'm2' ? '/м²' : ''}`,
  t.percent != null && `${t.percent}%`, t.months != null && `${t.months} мес.`, t.money === false && '(не предложение денег)',
].filter(Boolean).join(' ')
/** Пояснение кейса без служебных пометок (money=false — «не предложение денег»: уже сказано словами — убираем) */
const noteText = (s: string) => s.replace(/\s*\(money=false\)/g, '').replace(/:\s*money=false,?/g, ',')
  .replace(/money=false/g, 'не предложение денег').replace(/money=true/g, 'предложение денег')

/** Ожидание или результат кейса: ЖК (с уверенностью у результата) и условия оплаты */
function EvalCell({ v, names, got }: { v: Record<string, unknown>; names: Map<string, string>; got?: boolean }) {
  const o = v as EvalSide
  const cx = o.complexId
    ? `ЖК «${o.complexName ?? names.get(o.complexId) ?? o.complexId}»${got && o.confidence != null ? ` · ${pct(o.confidence)}${o.low ? ', низкая' : ''}` : ''}`
    : o.noHypotheses ? 'ЖК — гипотез нет' : got ? 'ЖК — гипотез нет' : 'ЖК — без уверенной привязки'
  return (
    <div className="ai-kv">
      <div>{cx}</div>
      {Array.isArray(o.terms) && <div className="c2">{o.terms.length ? o.terms.map(termShort).join(', ') : 'условий нет'}</div>}
    </div>
  )
}

/** Методика — по образцу раздела «Ипотека» на realty.yandex.ru: серая шапка с плитками, калькулятор, таблица программ.
 *  Всё — из общей модели shared/risk_model.json (та же, что считает бэкенд) */
export default function Method() {
  const { mode, complexes, riskOf, aiEval } = useStore()
  const live = mode === 'api'
  // проверка ИИ на новых данных — прогон на сервере при открытии страницы
  const [ev, setEv] = useState<{ data?: AiEval; err?: string } | null>(null)
  const [evVer, setEvVer] = useState(0)
  useEffect(() => {
    if (!live) return
    let alive = true
    aiEval().then((data) => { if (alive) setEv({ data }) }, (e: unknown) => { if (alive) setEv({ err: errorText(e) }) })
    return () => { alive = false }
  }, [live, evVer]) // eslint-disable-line react-hooks/exhaustive-deps
  const names = useMemo(() => new Map(complexes.map((c) => [c.id, c.name])), [complexes])
  // калькулятор: начинаем с кейса A — показывает, что «нет сведений» не превращается в R1
  const [st, setSt] = useState<Record<string, FactorState>>(() => fromCase(model.cases[0]))
  const calc = useMemo(() => {
    const hits = model.factors.filter((f) => st[f.code] === 'hit').map((f) => f.code)
    const unknown = model.factors.filter((f) => st[f.code] === 'unknown').map((f) => f.code)
    const score = Math.min(model.maxScore, hits.reduce((s, c) => s + weight(c), 0))
    return { hits, unknown, score, zone: zoneFor(score, hits) }
  }, [st])
  const zoneItem = model.zones.items.find((z) => z.zone === calc.zone)
  const pair = model.zones.redRequires.join('+')
  const sameZone = complexes.filter((c) => { const r = riskOf(c); return isAssessed(r) && r.zone === calc.zone }).length
  const loaded = model.cases.find((k) => model.factors.every((f) => fromCase(k)[f.code] === st[f.code]))
  const toCalc = (k: ModelCase) => { setSt(fromCase(k)); document.getElementById('calc')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }
  // таблица и словари
  const [open, setOpen] = useState<string | null>(model.factors[0]?.code ?? null)
  const [dict, setDict] = useState<Dict>('money')
  const d = DICTS.find((x) => x[0] === dict)!

  return (
    <>
      <div className="band">
        <div className="page-title" style={{ paddingBottom: 16 }}>
          <div>
            <div className="crumbs"><Link to="/">SAQ Qurylys</Link><span>›</span><span>Методика скоринга</span></div>
            <h1 className="t-h1">Как считается риск</h1>
            <div className="row t-body" style={{ gap: 8, marginTop: 12 }}><span style={{ color: 'var(--link)', display: 'flex' }}><IcInfo /></span><span>{model.disclaimer} <a href="#law" className="link">Нормативная база</a></span></div>
            <div className="row wrap-f t-sm" style={{ gap: 8, marginTop: 14 }}>
              <span className="c2">Контрольные сценарии ТЗ §5:</span>
              {model.cases.map((k) => {
                const c = complexes.find((x) => x.id === k.complexId)
                // ЖК сценария есть только в тестовом наборе (и в старых БД) — иначе сценарий открывается в калькуляторе
                return c ? <Link key={k.id} to={`/complexes/${c.id}?tab=risk`} state={from('Методика', '/method')} className="chip" style={{ height: 32 }}><b>{k.id}</b>ЖК «{c.name}»<span className="n">{k.score}</span></Link>
                  : <button key={k.id} type="button" className="chip" style={{ height: 32 }} onClick={() => toCalc(k)} title="Открыть сценарий в калькуляторе"><b>{k.id}</b>{zoneShort[k.zone]}<span className="n">{k.score}</span></button>
              })}
              <a href="#cases" className="link">Подробнее</a>
            </div>
          </div>
        </div>
        <Board kind="method">
          <Cap aside={`максимум ${model.maxScore} баллов · красная зона — от ${model.zones.redFrom} и только при R1 + R2`}>Из чего складывается балл</Cap>
          <div className="sb-weights">
            {model.factors.map((f) => {
              const Icon = ICONS[f.code] ?? IcHelp
              return (
                <a key={f.code} href={`#${f.code}`} className="sb-w" style={{ flexGrow: f.weight }} onClick={() => setOpen(f.code)} title={`${f.code} · ${f.short}: +${f.weight}`}>
                  <span className="sb-w-bar"><b>+{f.weight}</b></span>
                  <span className="sb-w-l"><Icon size={20} /><span>{f.short}</span></span>
                </a>
              )
            })}
          </div>
          <div className="sb-scale" aria-hidden>
            <i className="green" style={{ width: `${model.zones.yellowFrom}%` }} />
            <i className="yellow" style={{ width: `${model.zones.redFrom - model.zones.yellowFrom}%` }} />
            <i className="red" style={{ flex: 1 }} />
            {[0, model.zones.yellowFrom, model.zones.redFrom, model.maxScore].map((v) => <span key={v} style={{ left: `${(v / model.maxScore) * 100}%` }}>{v}</span>)}
          </div>
        </Board>

        <div className="calc" id="calc">
          <div className="calc-l">
            <div className="row between" style={{ marginBottom: 4 }}>
              <h2 className="t-h2">Посчитайте балл</h2>
              {loaded && <span className="tag gray">Кейс {loaded.id} из ТЗ</span>}
            </div>
            <p className="c2 t-sm" style={{ marginBottom: 12 }}>Отметьте, что известно об объекте. В балл идут только подтверждённые факторы</p>
            {model.factors.map((f) => {
              const [hit, clear, unknown] = labels(f.code)
              return (
                <div key={f.code} className="crow">
                  <span>{f.short} <i>{f.code} · +{f.weight}</i></span>
                  <Seg value={st[f.code] ?? 'clear'} onChange={(v) => setSt((s) => ({ ...s, [f.code]: v }))}
                    items={[['hit', hit], ['clear', clear], ['unknown', unknown]]} />
                </div>
              )
            })}
          </div>
          <div className="calc-r">
            <div className="row between"><span className="c2 t-sm">Риск-скоринг</span><ZoneTag zone={calc.zone} /></div>
            <div className="row" style={{ alignItems: 'flex-end', gap: 8, margin: '10px 0 6px' }}><span className="big-num">{calc.score}</span><span className="c2" style={{ paddingBottom: 6 }}>из {model.maxScore}</span></div>
            <div className="t-sm c2 num" style={{ marginBottom: 16 }}>{formula(calc.hits)}</div>
            <Gauge value={calc.score} />
            <div className="t-sm stack" style={{ margin: '18px 0', gap: 8, lineHeight: '20px' }}>
              <span>{zoneItem?.action}</span>
              {calc.zone === 'yellow' && calc.score >= model.zones.redFrom && (
                <span>Балл {model.zones.redFrom}+, но без пары {pair} зона остаётся жёлтой: красная — только когда подтверждены оба фактора.</span>
              )}
              {calc.unknown.length > 0 && (
                <span>
                  <span className="tag yellow" style={{ marginRight: 6 }}><IcHelp size={14} />Нужна проверка</span>
                  {calc.unknown.join(', ')}: неизвестное даёт 0 баллов{calc.unknown.includes('R1') ? ' и не превращается в R1' : ''}.
                </span>
              )}
            </div>
            <Link to={`/complexes?zone=${calc.zone}`} className="btn d full">Показать объекты в этой зоне · {sameZone}</Link>
          </div>
        </div>
        <p className="t-sm c2" style={{ marginTop: 16 }}>{model.formula} <span className="c3">Модель {model.version}</span></p>
      </div>

      <div className="zones3">
        {model.zones.items.map((z) => (
          <div key={z.zone} className={`ztile ${z.zone}`}>
            <span className="zdot" /><b>{z.name} зона</b><span className="big">{z.range}</span>
            <span className="c2 t-sm">{z.rule}</span>
            <span className="t-sm" style={{ marginTop: 8 }}>{z.action}</span>
          </div>
        ))}
      </div>

      <h2 className="t-h2" style={{ margin: '48px 0 8px' }}>Факторы риска</h2>
      <table className="ytable">
        <thead><tr><th style={{ width: 70 }}>Код</th><th>Фактор</th><th>Когда учитывается</th><th className="r">Баллы</th><th style={{ width: 40 }} /></tr></thead>
        <tbody>
          {model.factors.flatMap((f) => [
            <tr key={f.code} id={f.code} onClick={() => setOpen(open === f.code ? null : f.code)}>
              <td><span className="tag dark">{f.code}</span></td>
              <td><span className="link">{f.title}</span>{lawRefs(f.code).length > 0 && <div className="t-xs c2" style={{ marginTop: 4 }}>Основание: <LawRefs code={f.code} /></div>}</td>
              <td className="c2">{f.when}</td>
              <td className="r b" style={{ fontSize: 18 }}>+{f.weight}</td>
              <td className="r c2">{open === f.code ? <IcChevronUp size={16} /> : <IcChevronDown size={16} />}</td>
            </tr>,
            open === f.code && (
              <tr key={f.code + 'x'} className="xrow"><td />
                <td colSpan={4}>
                  <div className="xgrid"><div><div className="c2 t-sm">Откуда данные</div>{f.source}</div><div><div className="c2 t-sm">Пример</div>{f.example}</div></div>
                  <div style={{ marginTop: 12 }}>
                    <div className="c2 t-sm">Основание в законе</div>
                    {refWhy(f.code).length ? refWhy(f.code).map((r, i) => (
                      <div key={i} className="t-sm" style={{ marginTop: 4, lineHeight: '20px' }}>
                        <b>{refText(r)}</b> — {r.why}
                        <details style={{ marginTop: 2 }}>
                          <summary className="link t-xs" style={{ cursor: 'pointer' }}>Текст нормы</summary>
                          {excerptsFor(r).map((e) => <blockquote key={e.part} className="t-xs" style={{ margin: '4px 0 0', paddingLeft: 10, borderLeft: '3px solid var(--border)', whiteSpace: 'pre-line' }}>{e.text}</blockquote>)}
                        </details>
                      </div>
                    )) : <div className="t-sm c2" style={{ marginTop: 4 }}>Прямой нормы в статьях 7, 8, 18, 23 нет: фактор — признак для проверки по данным мониторинга (<a className="link" href="#law">Нормативная база</a>).</div>}
                  </div>
                </td>
              </tr>
            ),
          ])}
        </tbody>
      </table>

      <LawBlock />

      <h2 className="t-h2" style={{ margin: '48px 0 4px' }}>Дополнительные наблюдения — вне балла</h2>
      <p className="c2" style={{ marginBottom: 16, maxWidth: 760 }}>Повод присмотреться, но не факт нарушения по объекту. Показываются в карточке ЖК отдельно и на балл и зону не влияют.</p>
      <div className="promo-tiles">
        {model.observations.map((o) => (
          <div key={o.code} className="promo-tile">
            <span className="tag gray" style={{ alignSelf: 'flex-start' }}>{o.code}</span>
            <div className="h">{o.title}</div>
            <div className="d">{o.why}</div>
          </div>
        ))}
      </div>

      <h2 className="t-h2" id="cases" style={{ margin: '48px 0 4px' }}>Контрольные сценарии ТЗ</h2>
      <p className="c2" style={{ marginBottom: 16, maxWidth: 760 }}>Три эталонных кейса из ТЗ §5. Их проверяют автотесты на тестовом наборе — он не входит в данные приложения. Реальные ЖК стартового набора в сценариях не участвуют: кейс можно открыть в калькуляторе выше.</p>
      <div className="promo-tiles cases">
        {model.cases.map((k) => {
          const c = complexes.find((x) => x.id === k.complexId)
          const r = c && riskOf(c)
          const same = r && r.score === k.score && r.zone === k.zone
          return (
            <div key={k.id} className="promo-tile">
              <div className="row between"><span className="tag dark">Кейс {k.id}</span><ZoneTag zone={k.zone} /></div>
              <div className="h" style={{ marginTop: 4 }}>{k.title}</div>
              <div className="calc-line num">{formula(k.hits)}</div>
              {k.unknown.length > 0 && <div className="d">Нет сведений: {k.unknown.join(', ')} — 0 баллов</div>}
              <div className="d">{k.expect}</div>
              <div className="case-live">
                {c && r ? (
                  <>
                    <span className="t-sm" style={{ flex: 1, minWidth: 0 }}>ЖК «{c.name}»{isDemo(c) && <span className="c3"> · демо</span>}</span>
                    <RiskScore r={r} />
                    {same ? <span className="tag green"><IcCheck size={12} strokeWidth={3} />совпадает</span> : <span className="tag yellow" title="Данные объекта изменились после эталона сценария">отличается</span>}
                  </>
                ) : <span className="t-sm c3">Проверяется автотестом на тестовом наборе</span>}
              </div>
              <div className="row" style={{ gap: 8 }}>
                {c && <Link to={`/complexes/${c.id}?tab=risk`} state={from('Сценарии в методике', '/method#cases')} className="btn y m" style={{ flex: 1 }}>Открыть сценарий {k.id}</Link>}
                <button className="btn o m" style={{ flex: c ? 'none' : 1 }} onClick={() => toCalc(k)}>В калькулятор</button>
              </div>
            </div>
          )
        })}
      </div>

      <h2 className="t-h2 row" id="ai" style={{ margin: '48px 0 4px', gap: 10 }}>ИИ-анализ публикаций <AiBadge label="гипотезы ИИ" /></h2>
      <p className="c2" style={{ marginBottom: 16, maxWidth: 760 }}>
        ИИ работает на новых данных — тексте и изображениях публикаций, собранных подключёнными источниками, присланных через бота или
        добавленных аналитиком вручную. Движок офлайн и на сервере: распознавание, правила,
        словари и нечёткое сравнение, без внешних API и ключей. Одинаковый вход даёт одинаковый результат. Всё, что он находит, — гипотезы
        с фрагментом-подтверждением и уверенностью.
      </p>
      <div className="promo-tiles">
        {AI_TILES.map(([h, d]) => <div key={h} className="promo-tile"><div className="h">{h}</div><div className="d">{d}</div></div>)}
      </div>
      <div className="xgrid" style={{ marginTop: 16, gap: 16 }}>
        <div className="box t-sm" style={{ lineHeight: '20px' }}>
          <div className="bold" style={{ marginBottom: 6 }}>Решает аналитик</div>
          <div>· Принимает гипотезу привязки — привязка становится фактом с его именем и временем, R2 и R3 по публикации начинают идти в балл объекта.</div>
          <div>· Отклоняет гипотезу — ИИ больше не предлагает этот ЖК, текущая привязка-гипотеза снимается.</div>
          <div>· Подтверждает условия оплаты или отмечает «Это не предложение денег» — тогда R2 этой публикации не засчитывается.</div>
          <div>· Каждое решение — в журнале публикации: кто, что, когда.</div>
        </div>
        <div className="box warn t-sm" style={{ lineHeight: '20px' }}>
          <div className="bold" style={{ marginBottom: 6 }}>Чего ИИ не делает</div>
          <div>· Не выносит юридический вердикт и не подтверждает нарушение.</div>
          <div>· Не меняет балл сам: привязка-гипотеза даёт 0 баллов и «Нужна проверка», как бы ни была высока уверенность.</div>
          <div>· Не ставит R2: его ставит правило модели по словарю — воспроизводимая цитата. Снять R2 может только аналитик.</div>
          <div>· Неуверенное помечает: ниже {pct(T.lowConfidence)} — «низкая уверенность», ниже {pct(T.hypothesisMin)} гипотеза не показывается.</div>
        </div>
      </div>

      <h2 className="t-h2" id="ai-eval" style={{ margin: '48px 0 4px' }}>Проверка на новых данных</h2>
      <p className="c2" style={{ marginBottom: 16, maxWidth: 760 }}>
        Синтетические тестовые тексты — не из собранной рекламы: транслит и опечатки в названиях, адрес без названия, телефон застройщика, подборка нескольких ЖК, шумный
        OCR, «бесплатная бронь», вторичка в сданном доме и контрпримеры без признаков. Сервер прогоняет движок при открытии страницы; те же кейсы
        проверяет автотест.
      </p>
      {!live ? (
        <div className="ai-note">{mode === 'loading' ? 'Подключаемся к серверу…' : 'Прогон выполняется на сервере — в офлайн-копии его нет. Откройте страницу, когда сервер будет доступен.'}</div>
      ) : !ev ? <div className="ai-note">Прогоняем кейсы…</div>
        : !ev.data ? <div className="ai-note err">Проверка не загрузилась: {ev.err} <button className="link" onClick={() => setEvVer((v) => v + 1)}>Повторить</button></div>
          : (() => {
            const m = ev.data.metrics, okN = ev.data.cases.filter((k) => k.ok).length
            return (
              <>
                <div className="ai-metrics">
                  <div className="ai-metric"><b>{okN} из {ev.data.cases.length}</b><span>кейсов пройдено</span></div>
                  <div className="ai-metric"><b>{m.termsFound} из {m.termsExpected}</b><span>ожидаемых условий оплаты найдено</span></div>
                  <div className="ai-metric"><b>{pct(m.termsPrecision)}</b><span>точность условий: доля найденных, которые ожидались</span></div>
                  <div className="ai-metric"><b>{pct(m.linkTop1)}</b><span>верный ЖК — первая гипотеза ({m.linkCases} кейсов с ЖК)</span></div>
                  <div className="ai-metric"><b>{m.falsePositives}</b><span>ложных привязок на контрпримерах</span></div>
                </div>
                <table className="ytable">
                  <thead><tr><th style={{ width: 90 }}>Кейс</th><th>Текст</th><th>Ожидалось</th><th>Получено</th><th className="r">Итог</th></tr></thead>
                  <tbody>
                    {ev.data.cases.map((k) => (
                      <tr key={k.id}>
                        <td><span className="tag gray">{k.id}</span></td>
                        <td className="ai-text">
                          {k.title && <div className="bold" style={{ marginBottom: 2 }}>{k.title}</div>}
                          {k.text}
                          {k.ocr && <div className="c2" style={{ marginTop: 2 }}>OCR: {k.ocr}</div>}
                          {k.account && <div className="c2" style={{ marginTop: 2 }}>Аккаунт: {k.account}</div>}
                          {k.notes && <div className="t-xs" style={{ marginTop: 4, color: k.ok ? 'var(--text-2)' : 'var(--red-ink)' }}>{noteText(k.notes)}</div>}
                        </td>
                        <td><EvalCell v={k.expected} names={names} /></td>
                        <td><EvalCell v={k.got} names={names} got /></td>
                        <td className="r">{k.ok ? <span className="tag green"><IcCheck size={12} strokeWidth={3} />совпадает</span> : <span className="tag red"><IcClose size={12} strokeWidth={3} />расходится</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="t-sm c3" style={{ marginTop: 8 }}>Движок {ev.data.engine}</p>
              </>
            )
          })()}

      <h2 className="t-h2" style={{ margin: '48px 0 16px' }}>Словари для разбора рекламы</h2>
      <Seg value={dict} onChange={setDict} items={DICTS.map(([k, l]) => [k, <>{l}<span style={{ opacity: 0.56 }}>{model.markers[k].length}</span></>])} />
      <p className="c2" style={{ margin: '16px 0', maxWidth: 760 }}>{d[3]}</p>
      <div className="chips static">
        {model.markers[dict].map((k) => <span key={k} className={`chip ${d[2]}`}>{k}</span>)}
      </div>
      <p className="t-sm c3" style={{ marginTop: 16, maxWidth: 760 }}>
        Словари — часть модели (версия {model.version}): их читают и бэкенд, и интерфейс. Здесь они только для просмотра —
        правка меняет модель и проходит через эталонные тесты. Маркер — основа слова: «рассрочк» находит и «рассрочка», и «рассрочку».
      </p>

      <Faq title="Вопросы о методике"
        items={[
          ['Почему «нет данных» не считается отсутствием основания?', 'По ТЗ R1 ставится, только когда отсутствие гарантии ЕО и разрешения МИО установлено проверенными данными с источником и датой сверки. Нет ответа реестра или сведений — 0 баллов и отметка «Нужна проверка»: аналитик запрашивает документы, а не фиксирует нарушение.'],
          ['Почему «нет разрешения МИО» не значит «незаконно»?', <>Закон допускает разные способы организации долевого участия (ст. 7 п. 1): гарантию Единого оператора, участие в проекте банка второго уровня и привлечение денег после возведения каркаса. Договор о предоставлении гарантии ЕО сам является основанием и не требует разрешения МИО (ст. 8 п. 6); разрешение МИО нужно при двух других способах (ст. 18 п. 1). Поэтому R1 — только когда установлено отсутствие и гарантии, и разрешения. Подробнее — <a className="link" href="#law">Нормативная база</a>.</>],
          ['Что значит «Нужна проверка»?', 'Хотя бы один фактор нельзя ни подтвердить, ни исключить: нет сведений, обращение ещё не проверено или привязка публикации к ЖК — только гипотеза. Балл от этого не растёт, но объект попадает в фильтр «Нужна проверка».'],
          ['Как учитываются гипотезы ИИ?', 'Привязка публикации к ЖК — факт только после решения аналитика: он принял гипотезу ИИ, выбрал ЖК сам или подтвердил нарушение по публикации. До этого привязка — гипотеза ИИ, как бы ни была высока уверенность: R2 и R3 по такой публикации дают 0 баллов и отметку «Нужна проверка».'],
          ['Может ли ИИ поднять или снизить балл сам?', <>Нет. ИИ находит условия оплаты и предлагает ЖК — это гипотезы. Балл меняют подтверждённые данные и решения аналитика: принятая привязка добавляет R2/R3 публикации объекту, отметка «Это не предложение денег» снимает R2 этой публикации. Подробнее — в разделе <a className="link" href="#ai">ИИ-анализ публикаций</a>.</>],
          ['Совпадает ли расчёт на сервере и в интерфейсе?', 'Да. Бэкенд и интерфейс читают одну модель, а автотесты сверяют оба движка с одними и теми же эталонными результатами. Одинаковые подтверждённые данные дают одинаковый балл.'],
        ]} />
    </>
  )
}
