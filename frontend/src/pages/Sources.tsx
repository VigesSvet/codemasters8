import { useState, type ComponentType } from 'react'
import { Cell, PageTitle, Toggle } from '../components/ui'
import { publishedListMeta } from '../data/demo'
import { useStore } from '../lib/store'
import { IcDatabase, IcDoc, IcGlobe, IcMap, IcMegaphone, IcPhone, IcPlus, IcRefresh } from '../ui/icons'

type St = 'on' | 'demo' | 'plan'
const SRC: { id: string; name: string; note: string; st: St; icon: ComponentType<{ size?: number }>; req?: boolean }[] = [
  { id: 's1', name: 'Ручной ввод аналитика', note: 'Ссылка, текст, скриншот + OCR', st: 'on', icon: IcPlus, req: true },
  { id: 's2', name: 'Локальный набор данных', note: 'CSV/XLSX картотеки · демо-набор 14 ЖК', st: 'on', icon: IcDoc, req: true },
  { id: 's3', name: 'Перечень ГАСК', note: `Instagram ГАСК · ${publishedListMeta.count} объектов на ${publishedListMeta.updated.split('-').reverse().join('.')}`, st: 'demo', icon: IcDatabase },
  { id: 's4', name: 'Instagram', note: 'Публичные посты по словарю; персонализированная реклама не охватывается', st: 'demo', icon: IcMegaphone },
  { id: 's5', name: 'TikTok', note: 'Описание видео + OCR кадров', st: 'demo', icon: IcPhone },
  { id: 's6', name: 'Krisha.kz · OLX · KN.kz', note: 'Раздел «Новостройки», Усть-Каменогорск', st: 'plan', icon: IcGlobe },
  { id: 's7', name: 'Геопортал · 2ГИС', note: 'Координаты и контуры участков', st: 'plan', icon: IcMap },
]
const stTag: Record<St, [string, string]> = { on: ['green', 'Подключён'], demo: ['yellow', 'Демо-режим'], plan: ['gray', 'Планируется'] }

export default function Sources() {
  const { notify } = useStore()
  const [on, setOn] = useState<Record<string, boolean>>(Object.fromEntries(SRC.map((s) => [s.id, s.st !== 'plan'])))
  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Источники данных']]} title="Источники данных" sub="Система работает без закрытых государственных API. Обязательные источники — локальные данные и ручной ввод, парсеры соцсетей подключаются дополнительно."
        actions={<button className="btn g m" onClick={() => notify('Синхронизация запущена')}><IcRefresh size={20} />Обновить всё</button>} />
      <div className="card" style={{ maxWidth: 900 }}>
        {SRC.map((s) => (
          <Cell key={s.id} inset icon={<span className="ico"><s.icon size={20} /></span>}
            title={<span className="row" style={{ gap: 8 }}>{s.name}{s.req && <span className="tag dark">ТЗ</span>}</span>} sub={s.note}
            end={<><span className={`tag ${stTag[s.st][0]}`}>{stTag[s.st][1]}</span>{s.st !== 'plan' && <Toggle on={on[s.id]} onChange={(v) => { setOn((x) => ({ ...x, [s.id]: v })); notify(`${s.name}: сбор ${v ? 'включён' : 'выключен'}`) }} />}</>} />
        ))}
      </div>
    </>
  )
}
