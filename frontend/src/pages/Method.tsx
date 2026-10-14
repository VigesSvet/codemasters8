import { useState } from 'react'
import { CodeSnippet, DismissibleTag, Tag, TextInput, Button, StructuredListBody, StructuredListCell, StructuredListHead, StructuredListRow, StructuredListWrapper } from '@carbon/react'
import { Add } from '@carbon/icons-react'
import { PageHeader, ZoneTag } from '../components/ui'
import { keywordsDefault, keywordsSafe } from '../data/demo'

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
      <PageHeader crumbs={[['SAQ', '/'], ['Методика скоринга']]} title="Методика скоринга" sub="Скоринг сужает фокус аналитика, но не выносит решения. Каждый балл привязан к доказательству: документу, публикации или снимку." />
      <div className="page" style={{ maxWidth: 1200 }}>
        <div className="facts" style={{ gridTemplateColumns: 'repeat(3, 1fr)', marginTop: 0 }}>
          <div><ZoneTag zone="green" /><div className="v" style={{ marginTop: 12 }}>0 – 24 балла</div><div className="l" style={{ marginTop: 4 }}>Основания подтверждены, замечаний нет</div></div>
          <div><ZoneTag zone="yellow" /><div className="v" style={{ marginTop: 12 }}>25 – 59 баллов</div><div className="l" style={{ marginTop: 4 }}>Отклонения: простой, нет в перечне, неполные сведения</div></div>
          <div><ZoneTag zone="red" /><div className="v" style={{ marginTop: 12 }}>60+ и подтверждены R1 + R2</div><div className="l" style={{ marginTop: 4 }}>Реклама продажи без основания</div></div>
        </div>

        <h2 className="section-title">Факторы риска</h2>
        <StructuredListWrapper aria-label="Факторы">
          <StructuredListHead><StructuredListRow head><StructuredListCell head>Код</StructuredListCell><StructuredListCell head>Фактор</StructuredListCell><StructuredListCell head>Когда срабатывает</StructuredListCell><StructuredListCell head>Баллы</StructuredListCell></StructuredListRow></StructuredListHead>
          <StructuredListBody>
            {FACTORS.map(([code, t, p, d]) => (
              <StructuredListRow key={code}><StructuredListCell className="mono">{code}</StructuredListCell><StructuredListCell noWrap>{t}</StructuredListCell><StructuredListCell>{d}</StructuredListCell><StructuredListCell className="mono">+{p}</StructuredListCell></StructuredListRow>
            ))}
          </StructuredListBody>
        </StructuredListWrapper>

        <h2 className="section-title">Формула</h2>
        <CodeSnippet type="multi" feedback="Скопировано">{`score = min(100, Σ points(Rᵢ), где Rᵢ подтверждён)
zone  = red,    если score ≥ 60 и подтверждены R1 и R2
        yellow, если score ≥ 25
        green,  иначе
нет сведений по фактору → 0 баллов и отметка «Нужна проверка»`}</CodeSnippet>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48 }}>
          <div>
            <h2 className="section-title">Словарь продажи</h2>
            <p className="muted" style={{ marginBottom: 16 }}>Стартовые слова от ГАСК: «строительство», «квартира», «продаём», «продаётся», цена. Расширяйте связками.</p>
            <div style={{ display: 'flex', flexWrap: 'wrap' }}>{kw.map((k) => <DismissibleTag key={k} type="cool-gray" text={k} title="Убрать" onClose={() => setKw((x) => x.filter((y) => y !== k))} />)}</div>
            <div style={{ display: 'flex', alignItems: 'flex-end', marginTop: 16 }}>
              <TextInput id="nw" labelText="Новое слово или связка" value={nw} onChange={(e) => setNw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
              <Button hasIconOnly renderIcon={Add} iconDescription="Добавить" onClick={add} />
            </div>
          </div>
          <div>
            <h2 className="section-title">Признаки законности</h2>
            <p className="muted" style={{ marginBottom: 16 }}>Снижают оценку публикации: добросовестный застройщик обычно указывает основание.</p>
            <div style={{ display: 'flex', flexWrap: 'wrap' }}>{keywordsSafe.map((k) => <Tag key={k} type="green">{k}</Tag>)}</div>
          </div>
        </div>
      </div>
    </>
  )
}
