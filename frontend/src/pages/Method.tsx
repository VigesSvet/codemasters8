import { useMemo, useState, type ComponentType } from 'react'
import { Link } from 'react-router-dom'
import { Faq, Gauge, Score, Seg, ZoneTag } from '../components/ui'
import type { FactorState } from '../data/types'
import { factorDef, model, zoneFor, type ModelCase } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBank, IcCheck, IcChevronDown, IcChevronUp, IcClock, IcHelp, IcInfo, IcMegaphone, IcUsersFamily, IcWallet, IcWorker } from '../ui/icons'

const T = model.thresholds
const ICONS: Record<string, ComponentType<{ size?: number }>> = { R1: IcBank, R2: IcWallet, R3: IcMegaphone, R4: IcUsersFamily, R5: IcWorker, R6: IcClock }
// Подписи состояний в калькуляторе — по смыслу фактора: [подтверждён, нет, нет сведений]
const STATE_LABELS: Record<string, [string, string, string]> = {
  R1: ['Установлено отсутствие', 'Основание есть', 'Нет сведений'],
  R2: ['Есть в публикации', 'Нет', 'Привязка не подтверждена'],
  R3: ['Есть публикация', 'Нет', 'Привязка не подтверждена'],
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
/** «R2 25 + R3 10 = 35» — расчёт по подтверждённым факторам */
const formula = (hits: string[]) => {
  const sum = Math.min(model.maxScore, hits.reduce((s, c) => s + weight(c), 0))
  return hits.length ? `${hits.map((c) => `${c} ${weight(c)}`).join(' + ')} = ${sum}` : '0 баллов'
}
const fromCase = (k: ModelCase): Record<string, FactorState> =>
  Object.fromEntries(model.factors.map((f) => [f.code, k.hits.includes(f.code) ? 'hit' : k.unknown.includes(f.code) ? 'unknown' : 'clear']))

/** Методика — по образцу раздела «Ипотека» на realty.yandex.ru: серая шапка с плитками, калькулятор, таблица программ.
 *  Всё — из общей модели shared/risk_model.json (та же, что считает бэкенд) */
export default function Method() {
  const { complexes, riskOf } = useStore()
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
  const sameZone = complexes.filter((c) => riskOf(c).zone === calc.zone).length
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
            <div className="row t-body" style={{ gap: 8, marginTop: 12 }}><span style={{ color: 'var(--link)', display: 'flex' }}><IcInfo /></span>{model.disclaimer}</div>
          </div>
        </div>
        <div className="mtiles">
          {model.factors.map((f) => {
            const Icon = ICONS[f.code] ?? IcHelp
            return (
              <a key={f.code} href={`#${f.code}`} className="mtile" onClick={() => setOpen(f.code)}>
                <Icon size={32} />
                <span>{f.short}<b>+{f.weight}</b></span>
              </a>
            )
          })}
        </div>

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
              <td><span className="link">{f.title}</span></td>
              <td className="c2">{f.when}</td>
              <td className="r b" style={{ fontSize: 18 }}>+{f.weight}</td>
              <td className="r c2">{open === f.code ? <IcChevronUp size={16} /> : <IcChevronDown size={16} />}</td>
            </tr>,
            open === f.code && (
              <tr key={f.code + 'x'} className="xrow"><td />
                <td colSpan={4}>
                  <div className="xgrid"><div><div className="c2 t-sm">Откуда данные</div>{f.source}</div><div><div className="c2 t-sm">Пример</div>{f.example}</div></div>
                </td>
              </tr>
            ),
          ])}
        </tbody>
      </table>

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

      <h2 className="t-h2" style={{ margin: '48px 0 4px' }}>Контрольные сценарии ТЗ</h2>
      <p className="c2" style={{ marginBottom: 16, maxWidth: 760 }}>Три кейса из ТЗ §5 и объекты демо-набора, на которых они воспроизводятся. Живой балл считается по текущим данным.</p>
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
                    <Link to={`/complexes/${c.id}?tab=risk`} className="link t-sm" style={{ flex: 1, minWidth: 0 }}>Открыть ЖК «{c.name}»</Link>
                    <Score value={r.score} zone={r.zone} />
                    {same ? <span className="tag green"><IcCheck size={12} strokeWidth={3} />совпадает</span> : <span className="tag yellow" title="Данные объекта изменились после демо-набора">отличается</span>}
                  </>
                ) : <span className="t-sm c3">Объекта {k.complexId} нет в данных</span>}
              </div>
              <button className="btn o m" onClick={() => toCalc(k)}>В калькулятор</button>
            </div>
          )
        })}
      </div>

      <h2 className="t-h2" style={{ margin: '48px 0 16px' }}>Словари для разбора рекламы</h2>
      <Seg value={dict} onChange={setDict} items={DICTS.map(([k, l]) => [k, <>{l}<span style={{ opacity: 0.56 }}>{model.markers[k].length}</span></>])} />
      <p className="c2" style={{ margin: '16px 0', maxWidth: 760 }}>{d[3]}</p>
      <div className="chips static">
        {model.markers[dict].map((k) => <span key={k} className={`chip ${d[2]}`}>{k}</span>)}
      </div>
      <p className="t-sm c3" style={{ marginTop: 16, maxWidth: 760 }}>
        Словари — часть модели (shared/risk_model.json, версия {model.version}): их читают и бэкенд, и интерфейс. Здесь они только для просмотра —
        правка меняет модель и проходит через эталонные тесты. Маркер — основа слова: «рассрочк» находит и «рассрочка», и «рассрочку».
      </p>

      <Faq title="Вопросы о методике"
        items={[
          ['Почему «нет данных» не считается отсутствием основания?', 'По ТЗ R1 ставится, только когда отсутствие гарантии ЕО и разрешения МИО установлено проверенными данными с источником и датой сверки. Нет ответа реестра или сведений — 0 баллов и отметка «Нужна проверка»: аналитик запрашивает документы, а не фиксирует нарушение.'],
          ['Что значит «Нужна проверка»?', 'Хотя бы один фактор нельзя ни подтвердить, ни исключить: нет сведений, обращение ещё не проверено или привязка публикации к ЖК — только гипотеза. Балл от этого не растёт, но объект попадает в фильтр «Нужна проверка».'],
          ['Как учитываются гипотезы ИИ?', `Привязка публикации к ЖК — факт, если уверенность сопоставления от ${Math.round(T.linkConfirmed * 100)}% или аналитик подтвердил публикацию. Ниже — гипотеза: R2 и R3 по такой публикации дают 0 баллов и отметку «Нужна проверка».`],
          ['Совпадает ли расчёт на сервере и в интерфейсе?', 'Да. Бэкенд и интерфейс читают одну модель, а автотесты сверяют оба движка с одними и теми же эталонными результатами. Одинаковые подтверждённые данные дают одинаковый балл.'],
        ]} />
    </>
  )
}
