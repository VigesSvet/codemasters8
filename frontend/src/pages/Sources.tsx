import { Database, FileSpreadsheet, Globe, AtSign, Keyboard, Map, Music2, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { PageHead } from '../components/ui'
import { publishedListMeta } from '../data/demo'
import { useStore } from '../lib/store'

type St = 'on' | 'demo' | 'plan'
const SRC: { icon: ReactNode; name: string; kind: string; st: St; note: string; req?: boolean }[] = [
  { icon: <Keyboard size={18} />, name: 'Ручной ввод аналитика', kind: 'Публикации', st: 'on', note: 'Ссылка, текст, скриншот + OCR', req: true },
  { icon: <FileSpreadsheet size={18} />, name: 'Локальный набор данных (CSV/XLSX)', kind: 'ЖК, застройщики', st: 'on', note: 'Импорт картотеки, демо-набор 14 ЖК', req: true },
  { icon: <Database size={18} />, name: 'Перечень ГАСК (Instagram)', kind: 'Разрешённые ЖК', st: 'demo', note: `Обновлён ${publishedListMeta.updated}, ${publishedListMeta.count} объектов` },
  { icon: <AtSign size={18} />, name: 'Instagram — публичные профили', kind: 'Публикации', st: 'demo', note: 'Парсер публичных постов по словарю; персонализированная реклама не охватывается' },
  { icon: <Music2 size={18} />, name: 'TikTok', kind: 'Публикации', st: 'demo', note: 'Описание видео + OCR кадров' },
  { icon: <Globe size={18} />, name: 'Krisha.kz / OLX / KN.kz', kind: 'Объявления', st: 'plan', note: 'Раздел «Новостройки», Усть-Каменогорск' },
  { icon: <Map size={18} />, name: 'Геопортал / 2ГИС', kind: 'Координаты, контуры', st: 'plan', note: 'Подложка — OpenStreetMap / CARTO' },
]

export default function Sources() {
  const { notify } = useStore()
  return (
    <div className="page">
      <PageHead idx="M3" label="Откуда данные" title={<>Источники <span className="serif-i">данных</span></>}>
        <p className="ink2" style={{ maxWidth: 680, marginTop: 18 }}>Система работает без закрытых государственных API. Обязательные источники — локальные данные и ручной ввод; парсеры соцсетей подключаются дополнительно.</p>
      </PageHead>
      <div style={{ borderTop: '1px solid var(--line-strong)' }}>
        {SRC.map((s) => (
          <div key={s.name} style={{ display: 'grid', gridTemplateColumns: '48px 1.4fr 1fr 1.6fr 160px 110px', gap: 20, alignItems: 'center', padding: '20px 0', borderBottom: '1px solid var(--line)' }}>
            <div style={{ width: 40, height: 40, border: '1px solid var(--line)', display: 'grid', placeItems: 'center' }}>{s.icon}</div>
            <div><div style={{ fontWeight: 500, fontSize: 16 }}>{s.name}</div>{s.req && <span className="label" style={{ color: 'var(--ink)' }}>обязательный по ТЗ</span>}</div>
            <span className="label">{s.kind}</span>
            <span className="ink2">{s.note}</span>
            <span className={`zone z-${s.st === 'on' ? 'green' : s.st === 'demo' ? 'yellow' : 'red'}`}><i />{s.st === 'on' ? 'Подключён' : s.st === 'demo' ? 'Демо-режим' : 'Планируется'}</span>
            <button className="btn sm ghost" onClick={() => notify(`Синхронизация: ${s.name}`)}><RefreshCw size={12} />Обновить</button>
          </div>
        ))}
      </div>
    </div>
  )
}
