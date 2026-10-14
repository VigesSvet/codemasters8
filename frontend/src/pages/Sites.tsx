import { useState } from 'react'
import Compare from '../components/Compare'
import { Cell, PageTitle, fmtDate } from '../components/ui'
import type { Tri } from '../data/types'
import { useStore } from '../lib/store'
import { IcCheck, IcClose, IcDrone, IcHelp, IcUpload } from '../ui/icons'

export default function Sites() {
  const { complexes, notify } = useStore()
  const list = complexes.filter((c) => c.stage !== 'введён').sort((a, b) => b.idleDays - a.idleDays)
  const [sel, setSel] = useState(list.find((c) => c.photos.length > 2 && c.idleDays === 0)?.id ?? list[0].id)
  const c = complexes.find((x) => x.id === sel)!
  const last = c.photos[c.photos.length - 1]
  const sign = (k: string, v: Tri = 'unknown') => (
    <div className="tile" style={{ background: 'var(--bg)', minHeight: 0 }}>
      <span className="pic" style={{ height: 28 }}><span className={`zone-pic ${v === 'yes' ? 'green' : v === 'no' ? 'red' : 'all'}`} style={{ width: 28, height: 28, borderRadius: 9 }}>{v === 'yes' ? <IcCheck size={16} strokeWidth={3} /> : v === 'no' ? <IcClose size={16} strokeWidth={3} /> : <IcHelp size={16} />}</span></span>
      <span className="nm c2" style={{ fontSize: 13 }}>{k}</span><span className="vl">{v === 'yes' ? 'Есть' : v === 'no' ? 'Нет' : 'Не видно'}</span>
    </div>
  )

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Фото-мониторинг']]} title="Фото-мониторинг площадок"
        sub="Дополнительный модуль. Снимки с дрона или камеры сравниваются во времени: простой 90+ дней, ограждение, мусорный контейнер, мойка колёс. Управление дроном не входит в MVP."
        actions={<><button className="btn g m" onClick={() => notify('Заявка на облёт отправлена (демо)')}><IcDrone size={20} />Заявка на облёт</button><button className="btn y m"><IcUpload size={20} />Загрузить снимки</button></>} />
      <div className="cols">
        <div className="card sticky" style={{ maxHeight: 'calc(100vh - 120px)', overflow: 'auto' }}>
          {list.map((x) => (
            <Cell key={x.id} inset onClick={() => setSel(x.id)}
              icon={<img className="th" src={x.cover} alt="" style={{ width: 48, height: 48, borderRadius: 12, outline: sel === x.id ? '3px solid var(--yellow)' : undefined }} />}
              title={<span className={sel === x.id ? 'bold' : ''}>{x.name}</span>} sub={`${x.photos.length} снимков${x.photos.at(-1) ? ' · ' + fmtDate(x.photos.at(-1)!.date) : ''}`}
              end={<span className={`tag ${x.idleDays >= 90 ? 'red' : x.idleDays >= 30 ? 'yellow' : 'green'}`}>{x.idleDays ? `стоит ${x.idleDays} дн.` : 'идёт'}</span>} />
          ))}
        </div>
        <div className="stack fade" key={c.id} style={{ gap: 16 }}>
          <div className="card card-pad">
            <div className="row between" style={{ marginBottom: 16 }}><h2 className="t-h2">ЖК «{c.name}»</h2><span className="t-sm c2">{c.address}</span></div>
            {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={last} /> : c.photos.length === 1 ? <div className="ph" style={{ aspectRatio: '16/9', borderRadius: 20 }}><img src={c.photos[0].src} alt="" /></div> : <div className="drop">Снимков ещё нет</div>}
          </div>
          {last && (
            <div className="card">
              <div className="card-head"><h3>Последний снимок · {fmtDate(last.date)}</h3><span className={`tag ${last.activity === 'есть' ? 'green' : last.activity === 'низкая' ? 'yellow' : 'red'}`}>Активность: {last.activity}</span></div>
              <div className="tiles" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>{sign('Ограждение', last.fence)}{sign('Мусорный контейнер', last.bins)}{sign('Мойка колёс', last.wheelWash)}</div>
            </div>
          )}
          <div className="card">
            <div className="card-head"><h3>Хронология</h3></div>
            <div style={{ display: 'grid', gridAutoFlow: 'column', gridAutoColumns: '220px', gap: 10, overflowX: 'auto', padding: '4px 16px 16px' }}>
              {c.photos.map((p, i) => (
                <div key={i} style={{ background: 'var(--bg)', borderRadius: 20, overflow: 'hidden' }}>
                  <div className="ph" style={{ aspectRatio: '4/3', borderRadius: 16, margin: 4 }}><img src={p.src} alt="" /></div>
                  <div style={{ padding: '6px 12px 12px' }}><div className="bold t-sm">{fmtDate(p.date)}</div><div className="t-xs c2">{p.source} · {p.note ?? '—'}</div></div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

