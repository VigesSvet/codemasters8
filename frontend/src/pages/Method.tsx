import { useMemo, useState, type ComponentType } from 'react'
import { Link } from 'react-router-dom'
import { Gauge, Seg, Toggle, ZoneTag } from '../components/ui'
import { keywordsDefault, keywordsSafe } from '../data/demo'
import type { Zone } from '../data/types'
import { FUNDS_MARKERS, scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBank, IcChevronDown, IcChevronUp, IcClock, IcClose, IcDoc, IcInfo, IcMegaphone, IcUsersFamily, IcWallet } from '../ui/icons'

type F = { code: string; icon: ComponentType<{ size?: number }>; short: string; title: string; pts: string; when: string; source: string; example: string }
const FACTORS: F[] = [
  { code: 'R1', icon: IcBank, short: 'Нет основания', title: 'Нет основания для привлечения средств', pts: '35', when: 'Гарантии ЕО нет, и разрешение МИО вместе с банком не подтверждены', source: 'Картотека ГАСК, гарантии ЕО (КЖК), разрешения МИО', example: 'При действующей гарантии ЕО отсутствие отдельного разрешения МИО — не R1. Если сведений нет — 0 баллов и отметка «Нужна проверка».' },
  { code: 'R2', icon: IcMegaphone, short: 'Реклама продажи', title: 'Реклама продажи на стадии строительства', pts: '30', when: 'Найдена публикация с признаками продажи квартир, объект не введён в эксплуатацию', source: 'Лента публикаций: Instagram, TikTok, Krisha.kz, ручной ввод', example: '«Старт продаж! Квартиры от 290 000 ₸/м², бронь от 500 000 ₸» — объект на стадии фундамента.' },
  { code: 'R3', icon: IcDoc, short: 'Нет в перечне', title: 'Нет в опубликованном перечне ГАСК', pts: '10', when: 'ЖК отсутствует в перечне объектов, которым разрешено привлекать деньги', source: 'Перечень ГАСК ВКО (Instagram), сверка по названию и адресу', example: 'Само по себе не доказывает нарушение — только усиливает другие факторы.' },
  { code: 'R4', icon: IcClock, short: 'Стройка стоит', title: 'Простой строительства', pts: '5 / 10', when: 'На снимках нет изменений 30+ дней (5 баллов) или 90+ дней (10 баллов)', source: 'Фото-мониторинг: облёты дрона и стационарные камеры', example: 'Снимки 4 июня и 9 сентября совпадают: техника не двигалась 97 дней.' },
  { code: 'R5', icon: IcWallet, short: 'Сбор денег', title: 'Признаки сбора денег в тексте', pts: '10', when: 'В тексте или на картинке есть бронь, первый взнос, рассрочка, ДДУ', source: 'Текст публикации и распознанный текст со скриншота', example: '«Первый взнос 20%, договор долевого участия, успей по старой цене».' },
  { code: 'R6', icon: IcUsersFamily, short: 'Застройщик', title: 'Профиль застройщика', pts: '10', when: 'У застройщика есть незавершённые объекты при небольшом портфеле', source: 'Реестр юрлиц по БИН, история объектов', example: 'ТОО зарегистрировано в 2023 году, 1 объект, не сдан.' },
]

type Tri = 'yes' | 'no' | 'unknown'
const DICTS = { sale: 'Продажа', funds: 'Сбор денег', safe: 'Законность' } as const

/** Методика — по образцу раздела «Ипотека» на realty.yandex.ru: серая шапка с плитками, калькулятор, таблица программ */
export default function Method() {
  const { complexes, ads } = useStore()
  // калькулятор
  const [r1, setR1] = useState<Tri>('yes')
  const [r2, setR2] = useState(true)
  const [r3, setR3] = useState<Tri>('yes')
  const [r4, setR4] = useState<'0' | '30' | '90'>('0')
  const [r5, setR5] = useState(true)
  const [r6, setR6] = useState(false)
  const calc = useMemo(() => {
    const score = Math.min(100, (r1 === 'yes' ? 35 : 0) + (r2 ? 30 : 0) + (r3 === 'yes' ? 10 : 0) + (r4 === '90' ? 10 : r4 === '30' ? 5 : 0) + (r5 ? 10 : 0) + (r6 ? 10 : 0))
    const zone: Zone = score >= 60 && r1 === 'yes' && r2 ? 'red' : score >= 25 ? 'yellow' : 'green'
    const needs = r1 === 'unknown' || r3 === 'unknown'
    return { score, zone, needs }
  }, [r1, r2, r3, r4, r5, r6])
  const sameZone = complexes.filter((c) => scoreComplex(c, ads).zone === calc.zone).length
  // таблица
  const [open, setOpen] = useState<string | null>('R1')
  // словари
  const [dict, setDict] = useState<keyof typeof DICTS>('sale')
  const [words, setWords] = useState<Record<keyof typeof DICTS, string[]>>({ sale: keywordsDefault, funds: FUNDS_MARKERS, safe: keywordsSafe })
  const [nw, setNw] = useState('')
  const add = () => { if (nw.trim()) { setWords((w) => ({ ...w, [dict]: [...w[dict], nw.trim()] })); setNw('') } }

  const triSeg = (v: Tri, set: (t: Tri) => void) => <Seg value={v} onChange={set} items={[['yes', 'Да'], ['no', 'Нет'], ['unknown', 'Нет сведений']]} />

  return (
    <>
      <div className="band">
        <div className="page-title" style={{ paddingBottom: 16 }}>
          <div>
            <div className="crumbs"><Link to="/">SAQ Qurylys</Link><span>›</span><span>Методика скоринга</span></div>
            <h1 className="t-h1">Как считается риск</h1>
            <div className="row t-body" style={{ gap: 8, marginTop: 12 }}><span style={{ color: 'var(--link)', display: 'flex' }}><IcInfo /></span>Балл — подсказка для аналитика. Решение о нарушении принимает сотрудник</div>
          </div>
        </div>
        <div className="mtiles">
          {FACTORS.map((f) => (
            <a key={f.code} href={`#${f.code}`} className="mtile" onClick={() => setOpen(f.code)}>
              <f.icon size={32} />
              <span>{f.short}<b>+{f.pts}</b></span>
            </a>
          ))}
        </div>

        <div className="calc">
          <div className="calc-l">
            <h2 className="t-h2" style={{ marginBottom: 4 }}>Посчитайте балл</h2>
            <p className="c2 t-sm" style={{ marginBottom: 12 }}>Отметьте, что известно об объекте</p>
            <div className="crow"><span>Гарантия ЕО и разрешения <i>R1</i></span>{triSeg(r1 === 'yes' ? 'no' : r1 === 'no' ? 'yes' : 'unknown', (t) => setR1(t === 'yes' ? 'no' : t === 'no' ? 'yes' : 'unknown'))}</div>
            <div className="crow"><span>Есть реклама продажи <i>R2</i></span><Toggle on={r2} onChange={setR2} /></div>
            <div className="crow"><span>В перечне ГАСК <i>R3</i></span>{triSeg(r3 === 'yes' ? 'no' : r3 === 'no' ? 'yes' : 'unknown', (t) => setR3(t === 'yes' ? 'no' : t === 'no' ? 'yes' : 'unknown'))}</div>
            <div className="crow"><span>Стройка стоит <i>R4</i></span><Seg value={r4} onChange={setR4} items={[['0', 'Идёт'], ['30', '30+ дней'], ['90', '90+ дней']]} /></div>
            <div className="crow"><span>Бронь, взнос, ДДУ в тексте <i>R5</i></span><Toggle on={r5} onChange={setR5} /></div>
            <div className="crow"><span>Незавершённые объекты у застройщика <i>R6</i></span><Toggle on={r6} onChange={setR6} /></div>
          </div>
          <div className="calc-r">
            <div className="row between"><span className="c2 t-sm">Риск-скоринг</span><ZoneTag zone={calc.zone} /></div>
            <div className="row" style={{ alignItems: 'flex-end', gap: 8, margin: '10px 0 18px' }}><span className="big-num">{calc.score}</span><span className="c2" style={{ paddingBottom: 6 }}>из 100</span></div>
            <Gauge value={calc.score} />
            <p className="t-sm" style={{ margin: '18px 0', lineHeight: '20px' }}>
              {calc.zone === 'red' ? 'Подтверждены R1 и R2 — реклама продажи без основания. Объект попадёт в очередь первым.'
                : calc.zone === 'yellow' ? (calc.score >= 60 ? 'Балл высокий, но R1 и R2 вместе не подтверждены — поэтому зона жёлтая, а не красная.' : 'Есть отклонения — объект стоит проверить.')
                : 'Замечаний нет — объект не требует внимания.'}
              {calc.needs && ' Часть сведений не получена — будет отметка «Нужна проверка».'}
            </p>
            <Link to={`/complexes?zone=${calc.zone}`} className="btn d full">Показать объекты в этой зоне · {sameZone}</Link>
          </div>
        </div>
      </div>

      <div className="zones3">
        {([['green', 'Зелёная', '0–24', 'Основания подтверждены, замечаний нет'], ['yellow', 'Жёлтая', '25–59', 'или 60+, если R1 и R2 не подтверждены вместе'], ['red', 'Красная', '60+', 'и обязательно подтверждены R1 и R2']] as const).map(([z, n, r, d]) => (
          <div key={z} className={`ztile ${z}`}><span className="zdot" /><b>{n} зона</b><span className="big">{r}</span><span className="c2 t-sm">{d}</span></div>
        ))}
      </div>

      <h2 className="t-h2" style={{ margin: '48px 0 8px' }}>Факторы риска</h2>
      <table className="ytable">
        <thead><tr><th style={{ width: 70 }}>Код</th><th>Фактор</th><th>Когда срабатывает</th><th className="r">Баллы</th><th style={{ width: 40 }} /></tr></thead>
        <tbody>
          {FACTORS.flatMap((f) => [
            <tr key={f.code} id={f.code} onClick={() => setOpen(open === f.code ? null : f.code)}>
              <td><span className="tag dark">{f.code}</span></td>
              <td><span className="link">{f.title}</span></td>
              <td className="c2">{f.when}</td>
              <td className="r b" style={{ fontSize: 18 }}>+{f.pts}</td>
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

      <div className="row between" style={{ margin: '48px 0 16px' }}>
        <h2 className="t-h2">Словари для разбора рекламы</h2>
        <Seg value={dict} onChange={setDict} items={(Object.keys(DICTS) as (keyof typeof DICTS)[]).map((k) => [k, `${DICTS[k]} · ${words[k].length}`])} />
      </div>
      <p className="c2" style={{ marginBottom: 16 }}>
        {dict === 'sale' ? 'Стартовые слова ГАСК: «строительство», «квартира», «продаём», «продаётся», цена. Расширяйте связками.'
          : dict === 'funds' ? 'Признаки того, что застройщик собирает деньги до сдачи дома. Дают фактор R5.'
          : 'Добросовестный застройщик обычно указывает основание — такие слова снижают оценку публикации.'}
      </p>
      <div className="chips">
        {words[dict].map((k) => <span key={k} className={`chip ${dict === 'safe' ? 'safe' : dict === 'funds' ? 'fund' : ''}`}>{k}<button onClick={() => setWords((w) => ({ ...w, [dict]: w[dict].filter((y) => y !== k) }))} aria-label="Убрать" className="c3" style={{ display: 'flex' }}><IcClose size={12} /></button></span>)}
      </div>
      <div className="row" style={{ marginTop: 16, gap: 8, maxWidth: 560 }}>
        <input className="input" placeholder="Новое слово или связка" value={nw} onChange={(e) => setNw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
        <button className="btn g m" onClick={add}>Добавить</button>
      </div>
    </>
  )
}
