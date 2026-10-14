import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Tile } from '@carbon/react'
import { Drone, Upload } from '@carbon/icons-react'
import Compare from '../components/Compare'
import { PageHeader, TriTag, ZoneDot, fmtDate } from '../components/ui'
import { useStore } from '../lib/store'

export default function Sites() {
  const { complexes, notify } = useStore()
  const list = complexes.filter((c) => c.stage !== 'введён').sort((a, b) => b.idleDays - a.idleDays)
  const [sel, setSel] = useState(list.find((c) => c.photos.length > 2 && c.idleDays === 0)?.id ?? list[0].id)
  const c = complexes.find((x) => x.id === sel)!
  const last = c.photos[c.photos.length - 1]

  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Фото-мониторинг']]} title="Фото-мониторинг площадок"
        sub="Дополнительный модуль. Снимки с дрона или камеры сравниваются во времени: простой 90+ дней, ограждение, мусорный контейнер, мойка колёс. Управление дроном не входит в MVP."
        actions={<><Button kind="tertiary" renderIcon={Drone} onClick={() => notify('Заявка на облёт отправлена (демо)', 'info')}>Заявка на облёт</Button><Button renderIcon={Upload}>Загрузить снимки</Button></>} />
      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', minHeight: 'calc(100vh - 12rem)' }}>
        <div style={{ borderRight: '1px solid var(--cds-border-subtle-01)', background: 'var(--cds-layer-01)' }}>
          {list.map((x) => (
            <button key={x.id} className="obj-row" onClick={() => setSel(x.id)} style={{ gridTemplateColumns: '56px 1fr auto', background: sel === x.id ? 'var(--cds-layer-selected-01)' : undefined, boxShadow: sel === x.id ? 'inset 3px 0 0 var(--cds-interactive)' : undefined }}>
              <img src={x.cover} alt="" style={{ width: 56, height: 42 }} />
              <div><div className="t">{x.name}</div><div className="a">{x.photos.length} снимков · {x.photos.at(-1) ? fmtDate(x.photos.at(-1)!.date) : 'нет данных'}</div></div>
              <span className="label-01" style={{ display: 'flex', gap: 6, alignItems: 'center' }}><ZoneDot zone={x.idleDays >= 90 ? 'red' : x.idleDays >= 30 ? 'yellow' : 'green'} />{x.idleDays ? `${x.idleDays} дн.` : 'идёт'}</span>
            </button>
          ))}
        </div>
        <div className="page" key={c.id}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <h2 className="cds--type-productive-heading-04">ЖК «{c.name}»</h2>
            <Button kind="ghost" size="sm" as={Link} to={`/complexes/${c.id}`}>Карточка объекта</Button>
          </div>
          {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={last} /> : c.photos.length === 1 ? <img src={c.photos[0].src} alt="" style={{ width: '100%', aspectRatio: '16/9', objectFit: 'cover' }} /> : <Tile>Снимков ещё нет</Tile>}
          {last && (
            <div className="facts">
              <div><div className="l">Активность</div><div className="v" style={{ display: 'flex', gap: 8, alignItems: 'center' }}><ZoneDot zone={last.activity === 'есть' ? 'green' : last.activity === 'низкая' ? 'yellow' : 'red'} />{last.activity}</div></div>
              <div><div className="l">Ограждение</div><div className="v"><TriTag v={last.fence ?? 'unknown'} /></div></div>
              <div><div className="l">Мусорный контейнер</div><div className="v"><TriTag v={last.bins ?? 'unknown'} /></div></div>
              <div><div className="l">Мойка колёс</div><div className="v"><TriTag v={last.wheelWash ?? 'unknown'} /></div></div>
            </div>
          )}
          <div className="label-01" style={{ marginBottom: 8 }}>Хронология снимков</div>
          <div style={{ display: 'grid', gridAutoFlow: 'column', gridAutoColumns: '220px', gap: 16, overflowX: 'auto' }}>
            {c.photos.map((p, i) => (
              <div key={i} className="pcard" style={{ cursor: 'default' }}>
                <div className="ph"><img src={p.src} alt="" /></div>
                <div className="b"><div className="n mono">{fmtDate(p.date)}</div><div className="a">{p.source} · {p.note ?? '—'}</div></div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
