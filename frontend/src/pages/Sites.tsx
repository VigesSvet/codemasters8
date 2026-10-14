import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Camera, Plane, Upload } from 'lucide-react'
import Compare from '../components/Compare'
import { PageHead, TriMark, fmtDate } from '../components/ui'
import { useStore } from '../lib/store'

export default function Sites() {
  const { complexes, notify } = useStore()
  const withPhotos = complexes.filter((c) => c.stage !== 'введён').sort((a, b) => b.idleDays - a.idleDays)
  const [sel, setSel] = useState(withPhotos.find((c) => c.photos.length > 2 && c.idleDays === 0)?.id ?? withPhotos[0].id)
  const c = complexes.find((x) => x.id === sel)!
  const last = c.photos[c.photos.length - 1]

  return (
    <div className="page">
      <PageHead idx="M1" label="Дополнительный модуль · β" title={<>Фото-<span className="serif-i">мониторинг</span></>}
        aside={<><button className="btn ghost" onClick={() => notify('Задание на облёт отправлено (демо)')}><Plane size={14} />Запросить облёт</button><button className="btn primary"><Upload size={14} />Загрузить снимки</button></>}>
        <p className="ink2" style={{ maxWidth: 680, marginTop: 18 }}>Периодические снимки с дрона или стационарной камеры. Система сравнивает кадры во времени и отмечает простой площадки (например, нет изменений 90+ дней), а также наличие ограждения, мусорного контейнера и пункта мойки колёс. Управление дроном не входит в MVP.</p>
      </PageHead>

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 36 }}>
        <div style={{ borderTop: '1px solid var(--line-strong)' }}>
          {withPhotos.map((x) => (
            <button key={x.id} onClick={() => setSel(x.id)} style={{ display: 'grid', gridTemplateColumns: '1fr auto', width: '100%', textAlign: 'left', padding: '14px 12px', border: 0, borderBottom: '1px solid var(--line)', background: sel === x.id ? 'var(--surface)' : 'transparent', borderLeft: sel === x.id ? '2px solid var(--ink)' : '2px solid transparent' }}>
              <div><div style={{ fontWeight: 500 }}>{x.name}</div><div className="label" style={{ fontSize: 10, marginTop: 3 }}>{x.photos.length} снимков · {x.photos.at(-1) ? fmtDate(x.photos.at(-1)!.date) : 'нет данных'}</div></div>
              <span className={`zone z-${x.idleDays >= 90 ? 'red' : x.idleDays >= 30 ? 'yellow' : 'green'}`} style={{ alignSelf: 'center' }}><i />{x.idleDays ? `${x.idleDays} дн.` : 'идёт'}</span>
            </button>
          ))}
        </div>

        <div className="fade-in" key={c.id}>
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: 16 }}>
            <h2 className="display d-l">{c.name}</h2>
            <Link to={`/complexes/${c.id}`} className="btn ghost sm">Карточка ЖК →</Link>
          </div>
          {c.photos.length >= 2 ? <Compare before={c.photos[0]} after={last} /> : c.photos.length === 1 ? (
            <div className="ph-frame" style={{ aspectRatio: '16/10' }}><img className="img" src={c.photos[0].src} alt="" /></div>
          ) : <div className="drop" style={{ aspectRatio: '16/10' }}><div className="stack" style={{ alignItems: 'center' }}><Camera size={22} />Снимков ещё нет</div></div>}

          {last && (
            <div className="grid g-4" style={{ marginTop: 20, gap: 0, border: '1px solid var(--line)' }}>
              {[['Активность', <span className={`zone z-${last.activity === 'есть' ? 'green' : last.activity === 'низкая' ? 'yellow' : 'red'}`}><i />{last.activity}</span>], ['Ограждение', <TriMark v={last.fence ?? 'unknown'} />], ['Мусорный контейнер', <TriMark v={last.bins ?? 'unknown'} />], ['Мойка колёс', <TriMark v={last.wheelWash ?? 'unknown'} />]].map(([k, v], i) => (
                <div key={i} style={{ padding: 16, borderRight: i < 3 ? '1px solid var(--line)' : 0 }} className="stack"><span className="label">{k}</span>{v}</div>
              ))}
            </div>
          )}

          <div className="label" style={{ margin: '32px 0 14px' }}>Хронология снимков</div>
          <div style={{ display: 'grid', gridAutoFlow: 'column', gridAutoColumns: '220px', gap: 16, overflowX: 'auto', paddingBottom: 8 }}>
            {c.photos.map((p, i) => (
              <div key={i}>
                <div className="ph-frame" style={{ aspectRatio: '4/3' }}><img className="img" src={p.src} alt="" /></div>
                <div className="cap"><span className="mono">{fmtDate(p.date)}</span><span className="label">{p.source}</span></div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{p.note ?? '—'}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
