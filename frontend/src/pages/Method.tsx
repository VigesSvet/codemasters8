import { useState } from 'react'
import { PageTitle } from '../components/ui'
import { keywordsDefault, keywordsSafe } from '../data/demo'
import { IcBuilding, IcClose, IcPlus } from '../ui/icons'

const FACTORS = [
  ['R1', 'Нет основания для привлечения средств', '35', 'Гарантия ЕО отсутствует и разрешение МИО/БВУ не подтверждено. Отсутствие отдельного разрешения МИО при действующей гарантии — не R1. Нет сведений → 0 баллов.'],
  ['R2', 'Реклама продажи на стадии строительства', '30', 'Публикация с признаками продажи квартир, объект не введён в эксплуатацию, признаков законности в тексте нет.'],
  ['R3', 'Нет в опубликованном перечне ГАСК', '10', 'Перечень ЖК, которым разрешено привлекать средства. Само по себе не доказывает нарушение.'],
  ['R4', 'Простой строительства', '5 / 10', 'По снимкам нет активности 30+ дней (5) или 90+ дней (10).'],
  ['R5', 'Признаки сбора денег в тексте', '10', 'Бронь, первый взнос, рассрочка от застройщика, ДДУ, «цена котлована», «от фундамента».'],
  ['R6', 'Профиль застройщика', '10', 'Есть незавершённые объекты при небольшом портфеле.'],
]

export default function Method() {
  const [kw, setKw] = useState<string[]>(keywordsDefault)
  const [nw, setNw] = useState('')
  const add = () => { if (nw.trim()) { setKw((x) => [...x, nw.trim()]); setNw('') } }
  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Методика скоринга']]} title="Как считается риск" sub="Скоринг сужает фокус аналитика, но не выносит решения. Каждый балл привязан к доказательству: документу, публикации или снимку." />
      <div className="stack" style={{ gap: 16 }}>
        <div className="card">
          <div className="tiles">
            {([['green', 'Зелёная', '0–24 балла', 'Основания подтверждены, замечаний нет'], ['yellow', 'Жёлтая', '25–59 баллов', 'Простой, нет в перечне, неполные сведения'], ['red', 'Красная', '60+ и подтверждены R1 + R2', 'Реклама продажи без основания']] as const).map(([z, n, r, d]) => (
              <div key={z} className="tile" style={{ background: 'var(--bg)', minHeight: 140 }}>
                <span className="pic"><span className={`zone-pic ${z}`}><IcBuilding size={20} /></span></span>
                <span className="vl" style={{ fontSize: 20 }}>{n} зона</span>
                <span className="bold t-sm" style={{ marginTop: 4 }}>{r}</span>
                <span className="t-sm c2">{d}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Факторы</h3></div>
          {FACTORS.map(([code, t, p, d]) => (
            <div key={code} className="cell" style={{ alignItems: 'flex-start', paddingTop: 16, paddingBottom: 16 }}>
              <span className="tag dark" style={{ width: 40, justifyContent: 'center' }}>{code}</span>
              <div className="main"><div className="ttl bold">{t}</div><div className="sub" style={{ marginTop: 4, lineHeight: '20px' }}>{d}</div></div>
              <span className="t-h3 num">+{p}</span>
            </div>
          ))}
        </div>
        <div className="box" style={{ background: 'var(--dark)', color: '#fff', padding: '24px 28px' }}>
          <div className="t-sm" style={{ color: 'rgba(255,255,255,.6)', marginBottom: 10 }}>Формула</div>
          <div style={{ fontSize: 17, lineHeight: '28px' }}>
            риск = сумма баллов подтверждённых факторов, максимум 100<br />
            <span className="tag red" style={{ verticalAlign: 1 }}>красная</span> — 60 и больше <b>и</b> подтверждены R1 и R2 ·{' '}
            <span className="tag yellow" style={{ verticalAlign: 1 }}>жёлтая</span> — от 25 ·{' '}
            <span className="tag green" style={{ verticalAlign: 1 }}>зелёная</span> — меньше 25<br />
            нет сведений по фактору → 0 баллов и отметка «Нужна проверка»
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div className="card card-pad">
            <h3 className="t-h3">Словарь продажи</h3>
            <p className="t-sm c2" style={{ margin: '6px 0 16px' }}>Стартовые слова ГАСК: «строительство», «квартира», «продаём», «продаётся», цена. Расширяйте связками.</p>
            <div className="chips">{kw.map((k) => <span key={k} className="chip">{k}<button onClick={() => setKw((x) => x.filter((y) => y !== k))} aria-label="Убрать" className="c3"><IcClose size={14} /></button></span>)}</div>
            <div className="row" style={{ marginTop: 16, gap: 8 }}>
              <input className="input" placeholder="Новое слово или связка" value={nw} onChange={(e) => setNw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
              <button className="btn y m" onClick={add} aria-label="Добавить"><IcPlus size={20} /></button>
            </div>
          </div>
          <div className="card card-pad">
            <h3 className="t-h3">Признаки законности</h3>
            <p className="t-sm c2" style={{ margin: '6px 0 16px' }}>Снижают оценку публикации: добросовестный застройщик обычно указывает основание.</p>
            <div className="chips">{keywordsSafe.map((k) => <span key={k} className="chip" style={{ background: 'var(--green-bg)', color: '#13803a' }}>{k}</span>)}</div>
          </div>
        </div>
      </div>
    </>
  )
}
