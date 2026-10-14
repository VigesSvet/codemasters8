import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  Button, ProgressIndicator, ProgressStep, StructuredListBody, StructuredListCell, StructuredListHead, StructuredListRow, StructuredListWrapper,
  Tab, TabList, TabPanel, TabPanels, Tabs, Tag, Tile,
} from '@carbon/react'
import { Camera, DocumentPdf, Flag, Help, Launch } from '@carbon/icons-react'
import Compare from '../components/Compare'
import MapView from '../components/MapView'
import { Gauge, Highlight, NeedsCheck, PageHeader, PlatformIcon, StatusTag, TriTag, ZoneTag, fmtDate, fmtDT } from '../components/ui'
import { developers } from '../data/demo'
import type { Stage, Tri } from '../data/types'
import { scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

const STAGES: Stage[] = ['котлован', 'фундамент', 'каркас', 'кровля', 'отделка', 'введён']

/** Карточка объекта — по образцу страниц ЖК Korter / BS Holding: галерея, факты, вкладки, боковая карточка */
export default function ComplexCard() {
  const { id } = useParams()
  const { complexes, ads, notify } = useStore()
  const [tab, setTab] = useState(0)
  const c = complexes.find((x) => x.id === id)
  if (!c) return <div className="page">Объект не найден</div>
  const r = scoreComplex(c, ads)
  const dev = developers.find((d) => d.id === c.developerId)
  const linked = ads.filter((a) => a.complexId === c.id)
  const photos = [c.cover, ...c.photos.map((p) => p.src), '/img/site-b.jpg', '/img/city-panorama.jpg'].filter((v, i, a) => a.indexOf(v) === i)
  const stageIdx = STAGES.indexOf(c.stage)

  const basisRow = (title: string, v: Tri, ref?: string, note?: string) => (
    <StructuredListRow>
      <StructuredListCell noWrap>{title}</StructuredListCell>
      <StructuredListCell><TriTag v={v} /></StructuredListCell>
      <StructuredListCell>{ref ?? (v === 'unknown' ? <span className="muted">Запросить у уполномоченного органа</span> : '—')}{note && <div className="helper">{note}</div>}</StructuredListCell>
    </StructuredListRow>
  )

  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Реестр ЖК', '/complexes'], [c.name]]} title={`ЖК «${c.name}»`}
        sub={<>{c.address}, {c.district} · {dev?.name} · БИН <span className="mono">{dev?.bin}</span></>}
        actions={<>
          <Button kind="tertiary" renderIcon={DocumentPdf} onClick={() => notify('Справка по объекту сформирована')}>Справка PDF</Button>
          <Button kind="primary" renderIcon={Flag} onClick={() => notify('Кейс создан и добавлен в очередь')}>Создать кейс</Button>
        </>}>
        <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
          <ZoneTag zone={r.zone} />
          <Tag type="cool-gray">{c.stage}</Tag>
          <Tag type="cool-gray">Сдача {fmtDate(c.plannedEnd)}</Tag>
          {r.needsCheck && <NeedsCheck />}
        </div>
      </PageHeader>

      <div className="page">
        <div className="obj-layout">
          <div>
            <div className="gallery">
              {photos.slice(0, 3).map((p, i) => (
                <div key={i}>
                  <img src={p} alt="" />
                  {i === 2 && c.photos.length > 2 && <div className="more">+{c.photos.length - 2} снимка</div>}
                </div>
              ))}
            </div>

            <div className="facts">
              <div><div className="l">Этажность</div><div className="v">{c.floors} этажей</div></div>
              <div><div className="l">Квартир</div><div className="v">{c.flats}</div></div>
              <div><div className="l">Начало строительства</div><div className="v">{fmtDate(c.start)}</div></div>
              <div><div className="l">Плановая сдача</div><div className="v">{fmtDate(c.plannedEnd)}</div></div>
            </div>

            <ProgressIndicator currentIndex={stageIdx} spaceEqually>
              {STAGES.map((s, i) => <ProgressStep key={s} label={s[0].toUpperCase() + s.slice(1)} secondaryLabel={i === stageIdx ? `${c.progress}%` : undefined} complete={i < stageIdx} />)}
            </ProgressIndicator>

            <Tabs selectedIndex={tab} onChange={({ selectedIndex }) => setTab(selectedIndex)}>
              <TabList aria-label="Разделы объекта" style={{ marginTop: 32 }}>
                <Tab>Основания</Tab>
                <Tab>Реклама · {linked.length}</Tab>
                <Tab>Фото-мониторинг · {c.photos.length}</Tab>
                <Tab>Застройщик</Tab>
              </TabList>
              <TabPanels>
                <TabPanel>
                  <StructuredListWrapper aria-label="Основания привлечения средств">
                    <StructuredListHead>
                      <StructuredListRow head>
                        <StructuredListCell head>Основание</StructuredListCell>
                        <StructuredListCell head>Статус</StructuredListCell>
                        <StructuredListCell head>Документ / источник</StructuredListCell>
                      </StructuredListRow>
                    </StructuredListHead>
                    <StructuredListBody>
                      {basisRow('Гарантия ЕО (КЖК)', c.basis.guarantee, c.basis.guaranteeRef)}
                      {basisRow('Разрешение МИО', c.basis.mio, c.basis.mioRef, c.basis.guarantee === 'yes' && c.basis.mio !== 'yes' ? 'При действующей гарантии ЕО отсутствие отдельного разрешения МИО не является R1' : undefined)}
                      {basisRow('Банк второго уровня', c.basis.bvu, c.basis.bank)}
                      {basisRow('Перечень ГАСК', c.inPublishedList, c.inPublishedList === 'yes' ? 'Instagram ГАСК ВКО, перечень от 01.10.2026' : undefined, c.inPublishedList === 'no' ? 'Отсутствие в перечне само по себе не доказывает нарушение' : undefined)}
                    </StructuredListBody>
                  </StructuredListWrapper>
                </TabPanel>
                <TabPanel>
                  {linked.length === 0 && <p className="muted">Публикаций по объекту не найдено</p>}
                  {linked.map((a) => (
                    <Link key={a.id} to={`/ads/${a.id}`} style={{ display: 'grid', gridTemplateColumns: '88px 1fr auto', gap: 16, padding: '16px 0', borderBottom: '1px solid var(--cds-border-subtle-01)', textDecoration: 'none', color: 'inherit' }}>
                      {a.image ? <img src={a.image} alt="" style={{ width: 88, height: 88, objectFit: 'cover' }} /> : <div style={{ width: 88, height: 88, background: 'var(--cds-layer-02)' }} />}
                      <div>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><PlatformIcon p={a.platform} /><span className="label-01">{a.account} · {fmtDT(a.date)}</span></div>
                        <p style={{ marginTop: 8 }}><Highlight text={a.text} /></p>
                      </div>
                      <StatusTag s={a.status} />
                    </Link>
                  ))}
                </TabPanel>
                <TabPanel>
                  {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={c.photos[c.photos.length - 1]} /> : c.photos.length === 0 ? (
                    <Tile><Camera size={24} /><p style={{ marginTop: 8 }}>Снимков нет. Загрузите фото с дрона или камеры.</p></Tile>
                  ) : null}
                  <div className="cards" style={{ marginTop: 16, gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))' }}>
                    {c.photos.map((p, i) => (
                      <div key={i} className="pcard" style={{ cursor: 'default' }}>
                        <div className="ph"><img src={p.src} alt="" /></div>
                        <div className="b">
                          <div className="n mono">{fmtDate(p.date)}</div>
                          <div className="a">{p.source} · активность: {p.activity}</div>
                          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                            {p.fence && <TriTag v={p.fence} label="Ограждение" />}{p.bins && <TriTag v={p.bins} label="Контейнер" />}{p.wheelWash && <TriTag v={p.wheelWash} label="Мойка колёс" />}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </TabPanel>
                <TabPanel>
                  {dev && (
                    <StructuredListWrapper aria-label="Застройщик">
                      <StructuredListBody>
                        {[['Наименование', dev.name], ['БИН', dev.bin], ['Руководитель', dev.director], ['Дата регистрации', fmtDate(dev.registered)], ['Объектов всего', dev.objects], ['Незавершённых', dev.unfinished], ['Сайт', dev.site ?? '—'], ['Instagram', dev.instagram ?? '—']].map(([k, v]) => (
                          <StructuredListRow key={String(k)}><StructuredListCell noWrap>{k}</StructuredListCell><StructuredListCell>{v}</StructuredListCell></StructuredListRow>
                        ))}
                      </StructuredListBody>
                    </StructuredListWrapper>
                  )}
                </TabPanel>
              </TabPanels>
            </Tabs>
          </div>

          <aside className="sticky">
            <div className="risk-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}><span className="label-01">Риск-скоринг</span><ZoneTag zone={r.zone} size="sm" /></div>
              <div className="risk-big"><span className="v">{r.score}</span><span className="muted">/ 100</span></div>
              <Gauge value={r.score} />
              <p style={{ margin: '16px 0 8px' }}>{r.summary}</p>
              {r.factors.map((f) => (
                <div key={f.code} className={`factor ${f.state}`}><span className="code">{f.code}</span><div>{f.title}<div className="e">{f.evidence}</div></div><span className="pts">+{f.points}</span></div>
              ))}
              <div style={{ display: 'grid', gap: 1, marginTop: 16 }}>
                <Button kind="primary" renderIcon={Flag} style={{ maxWidth: 'none' }} onClick={() => notify('Кейс создан и добавлен в очередь')}>Создать кейс проверки</Button>
                <Button kind="secondary" renderIcon={Help} style={{ maxWidth: 'none' }} onClick={() => notify('Запрос сведений сформирован', 'info')}>Запросить сведения</Button>
                <Button kind="ghost" renderIcon={Launch} style={{ maxWidth: 'none' }} as={Link} to="/method">Как считается балл</Button>
              </div>
              <p className="helper" style={{ marginTop: 12 }}>ИИ даёт рекомендацию. Решение о нарушении принимает аналитик.</p>
            </div>
            <div style={{ height: 220, position: 'relative', marginTop: 16 }}>
              <MapView items={[{ c, zone: r.zone, score: r.score }]} center={[c.lng, c.lat]} zoom={14} interactive={false} labels />
            </div>
          </aside>
        </div>
      </div>
    </>
  )
}
