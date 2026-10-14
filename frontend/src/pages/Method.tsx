import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import { PageHead } from '../components/ui'
import { keywordsDefault, keywordsSafe } from '../data/demo'

const FACTORS = [
  ['R1', 'Нет основания для привлечения средств', 35, 'Гарантия ЕО отсутствует и разрешение МИО/БВУ не подтверждено. Отсутствие отдельного разрешения МИО при действующей гарантии — не R1. Нет сведений → 0 баллов.'],
  ['R2', 'Реклама продажи на стадии строительства', 30, 'Найдена публикация с признаками продажи квартир, объект не введён в эксплуатацию, признаков законности в тексте нет.'],
  ['R3', 'Отсутствует в опубликованном перечне ГАСК', 10, 'Перечень ЖК, которым разрешено привлекать средства. Само по себе не доказывает нарушение.'],
  ['R4', 'Простой строительства', '5 / 10', 'По снимкам нет активности 30+ дней (5) или 90+ дней (10).'],
  ['R5', 'Признаки сбора денег в тексте', 10, 'Бронь, первый взнос, рассрочка от застройщика, ДДУ, «цена котлована», «от фундамента».'],
  ['R6', 'Профиль застройщика', 10, 'Есть незавершённые объекты при небольшом портфеле.'],
] as const

export default function Method() {
  const [kw, setKw] = useState<string[]>(keywordsDefault)
  const [nw, setNw] = useState('')
  return (
    <div className="page">
      <PageHead idx="M2" label="Объяснимость" title={<>Как считается <span className="serif-i">риск</span></>}>
        <p className="ink2" style={{ maxWidth: 680, marginTop: 18 }}>Скоринг сужает фокус аналитика, но не выносит решения. Каждый балл привязан к конкретному доказательству — документу, публикации или снимку.</p>
      </PageHead>

      <div className="grid g-3" style={{ gap: 0, borderTop: '1px solid var(--line-strong)', borderBottom: '1px solid var(--line)' }}>
        {([['green', '0 – 24', 'Зелёная', 'Основания подтверждены, замечаний нет'], ['yellow', '25 – 59', 'Жёлтая', 'Есть отклонения: медленная стройка, нет в перечне, неполные сведения'], ['red', '60+ и R1 + R2', 'Красная', 'Реклама продажи без основания — подтверждены оба признака']] as const).map(([z, range, n, d], i) => (
          <div key={z} className={`z-${z}`} style={{ padding: '28px 24px', borderRight: i < 2 ? '1px solid var(--line)' : 0 }}>
            <div style={{ height: 6, background: 'var(--z)', marginBottom: 22 }} />
            <div className="label">{range}</div>
            <div className="display d-l" style={{ margin: '10px 0' }}>{n}</div>
            <div className="ink2">{d}</div>
          </div>
        ))}
      </div>

      <h2 className="display d-l" style={{ margin: '56px 0 8px' }}>Факторы</h2>
      <div style={{ borderTop: '1px solid var(--line-strong)' }}>
        {FACTORS.map(([code, title, pts, desc]) => (
          <div key={code} style={{ display: 'grid', gridTemplateColumns: '90px 1fr 1.4fr 90px', gap: 24, padding: '22px 0', borderBottom: '1px solid var(--line)', alignItems: 'start' }}>
            <span className="num" style={{ fontSize: 40 }}>{code}</span>
            <div style={{ fontSize: 18, fontWeight: 500 }}>{title}</div>
            <div className="ink2">{desc}</div>
            <span className="mono" style={{ textAlign: 'right', fontSize: 16 }}>+{pts}</span>
          </div>
        ))}
      </div>
      <div className="card card-pad mono" style={{ marginTop: 24, fontSize: 13 }}>
        score = min(100, Σ points(Rᵢ) где Rᵢ подтверждён)&nbsp;&nbsp;·&nbsp;&nbsp;zone = red если score ≥ 60 ∧ R1 ∧ R2; yellow если score ≥ 25; иначе green&nbsp;&nbsp;·&nbsp;&nbsp;нет сведений → 0 и «Нужна проверка»
      </div>

      <div className="grid g-2" style={{ marginTop: 56, gap: 48 }}>
        <div>
          <h3 className="display d-m">Словарь <span className="serif-i">продажи</span></h3>
          <p className="muted">Стартовые слова от ГАСК: «строительство», «квартира», «продаём», «продаётся», цена. Расширяйте связками.</p>
          <div className="row wrap" style={{ gap: 6, marginTop: 14 }}>
            {kw.map((k) => <span key={k} className="chip">{k}<button onClick={() => setKw((x) => x.filter((y) => y !== k))} style={{ border: 0, background: 'none', padding: 0, display: 'flex' }}><X size={11} /></button></span>)}
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <input className="input" style={{ height: 34 }} placeholder="Новое слово или связка" value={nw} onChange={(e) => setNw(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && nw) { setKw((x) => [...x, nw]); setNw('') } }} />
            <button className="btn" onClick={() => { if (nw) { setKw((x) => [...x, nw]); setNw('') } }}><Plus size={14} /></button>
          </div>
        </div>
        <div>
          <h3 className="display d-m">Признаки <span className="serif-i">законности</span></h3>
          <p className="muted">Снижают оценку публикации: добросовестный застройщик обычно указывает основание.</p>
          <div className="row wrap" style={{ gap: 6, marginTop: 14 }}>{keywordsSafe.map((k) => <span key={k} className="chip" style={{ borderColor: 'var(--green)', color: 'var(--green)' }}>{k}</span>)}</div>
        </div>
      </div>
    </div>
  )
}
