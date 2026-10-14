import { useState, type ReactNode } from 'react'
import { Faq, Hero, HeroTile, PlatformIcon, Seg, Toggle } from '../components/ui'
import { publishedListMeta } from '../data/demo'
import type { Platform } from '../data/types'
import { useStore } from '../lib/store'
import { IcCheck, IcClock, IcDatabase, IcDoc, IcMap, IcPencil, IcRefresh, IcShield } from '../ui/icons'

type St = 'on' | 'demo' | 'plan'
type Src = { id: string; name: string; what: string; how: string; st: St; icon: ReactNode; req?: boolean; count: string; upd: string }
const gray = (n: ReactNode) => <span className="zone-pic gray" style={{ width: 48, height: 48, borderRadius: 14 }}>{n}</span>
const brand = (p: Platform) => <PlatformIcon p={p} size={48} />
const SRC: Src[] = [
  { id: 's1', name: 'Ручной ввод', what: 'Публикации', how: 'Аналитик вставляет ссылку, текст и скриншот — текст с картинки распознаётся', st: 'on', icon: gray(<IcPencil size={22} />), req: true, count: '2 публ.', upd: 'сразу' },
  { id: 's2', name: 'Картотека (CSV/XLSX)', what: 'ЖК и застройщики', how: 'Импорт реестра объектов: адрес, стадия, основания, БИН', st: 'on', icon: gray(<IcDoc size={22} />), req: true, count: '14 ЖК', upd: 'при загрузке' },
  { id: 's3', name: 'Перечень ГАСК', what: 'Разрешённые ЖК', how: 'Список объектов с гарантией из Instagram ГАСК ВКО, сверка по названию и адресу', st: 'demo', icon: gray(<IcShield size={22} />), count: `${publishedListMeta.count} ЖК`, upd: publishedListMeta.updated.split('-').reverse().join('.') },
  { id: 's4', name: 'Instagram', what: 'Публикации', how: 'Публичные посты и Reels по словарю продажи. Персонализированную рекламу не охватывает', st: 'demo', icon: brand('instagram'), count: '5 публ.', upd: 'раз в час' },
  { id: 's5', name: 'TikTok', what: 'Публикации', how: 'Описание видео и текст с кадров', st: 'demo', icon: brand('tiktok'), count: '2 публ.', upd: 'раз в час' },
  { id: 's6', name: 'Telegram', what: 'Публикации', how: 'Открытые каналы продаж новостроек Өскемена', st: 'demo', icon: brand('telegram'), count: '1 публ.', upd: 'раз в час' },
  { id: 's7', name: 'Krisha.kz', what: 'Объявления', how: 'Раздел «Новостройки» и переуступки, Усть-Каменогорск', st: 'plan', icon: brand('krisha'), count: '—', upd: '—' },
  { id: 's8', name: 'OLX', what: 'Объявления', how: 'Продажа квартир в строящихся домах', st: 'plan', icon: brand('olx'), count: '—', upd: '—' },
  { id: 's9', name: '2ГИС · геопортал', what: 'Координаты', how: 'Точки и контуры участков для карты', st: 'plan', icon: brand('2gis'), count: '—', upd: '—' },
]
const stTag: Record<St, [string, string]> = { on: ['green', 'Подключён'], demo: ['yellow', 'Демо-режим'], plan: ['gray', 'Планируется'] }

/** Источники — шапка со статусами, плитки-подключения как «Банки» на странице ипотеки, FAQ */
export default function Sources() {
  const { notify } = useStore()
  const [on, setOn] = useState<Record<string, boolean>>(Object.fromEntries(SRC.map((s) => [s.id, s.st !== 'plan'])))
  const [f, setF] = useState<St | 'all'>('all')
  const list = SRC.filter((s) => f === 'all' || s.st === f)
  const n = (st: St) => SRC.filter((s) => s.st === st).length

  return (
    <>
      <Hero crumb="Источники данных" title="Откуда берутся данные"
        info="Без закрытых государственных API. Обязательные по ТЗ — картотека и ручной ввод, парсеры соцсетей подключаются дополнительно"
        actions={<button className="btn y m" onClick={() => notify('Синхронизация запущена')}><IcRefresh />Обновить всё</button>}
        tiles={<>
          <HeroTile icon={<IcDatabase size={32} />} label="Всего источников" value={SRC.length} onClick={() => setF('all')} active={f === 'all'} />
          <HeroTile icon={<IcCheck size={32} />} label="Подключены" value={n('on')} tone="green" onClick={() => setF('on')} active={f === 'on'} />
          <HeroTile icon={<IcClock size={32} />} label="В демо-режиме" value={n('demo')} tone="yellow" onClick={() => setF('demo')} active={f === 'demo'} />
          <HeroTile icon={<IcMap size={32} />} label="Планируются" value={n('plan')} onClick={() => setF('plan')} active={f === 'plan'} />
          <HeroTile icon={<IcDoc size={32} />} label="Публикаций собрано" value={12} tone="blue" to="/ads" />
          <HeroTile icon={<IcRefresh size={32} />} label="Последний сбор" value="23:10" onClick={() => notify('Журнал сборов (демо)')} />
        </>} />

      <div className="row between" style={{ marginBottom: 16 }}>
        <h2 className="t-h2">Подключения</h2>
        <Seg value={f} onChange={setF} items={[['all', 'Все'], ['on', 'Подключены'], ['demo', 'Демо'], ['plan', 'Планируются']]} />
      </div>
      <div className="gcards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 16 }}>
        {list.map((s) => (
          <div key={s.id} className="devcard" style={{ cursor: 'default', opacity: s.st === 'plan' ? 0.75 : 1 }}>
            <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
              {s.icon}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row" style={{ gap: 8 }}><span className="t-h3" style={{ fontSize: 18 }}>{s.name}</span>{s.req && <span className="tag dark">ТЗ</span>}</div>
                <div className="t-sm c2" style={{ marginTop: 2 }}>{s.what}</div>
              </div>
              {s.st !== 'plan' && <Toggle on={on[s.id]} onChange={(v) => { setOn((x) => ({ ...x, [s.id]: v })); notify(`${s.name}: сбор ${v ? 'включён' : 'выключен'}`) }} />}
            </div>
            <div className="t-sm" style={{ lineHeight: '19px', minHeight: 38 }}>{s.how}</div>
            <div className="row between">
              <span className={`tag ${stTag[s.st][0]}`}>{stTag[s.st][1]}</span>
              <span className="t-sm c2">{s.count} · {s.upd}</span>
            </div>
          </div>
        ))}
      </div>

      <Faq title="Вопросы об источниках"
        items={[
          ['Почему реклама попадает не вся?', 'Таргетированную рекламу соцсети показывают конкретным людям — публичным сбором её не увидеть. Поэтому главный канал — ручное добавление: аналитик или житель присылает скриншот.'],
          ['Нужен ли доступ к закрытым госреестрам?', 'Нет. Система работает на картотеке ГАСК, которую загружают файлом, и на открытых данных. Если реестр откроют по API — его можно подключить как ещё один источник.'],
          ['Как связывается публикация с ЖК?', 'По названию ЖК и адресу в тексте и на картинке. Если уверенность низкая, аналитик выбирает объект вручную.'],
          ['Что значит «демо-режим»?', 'Источник работает на тестовом наборе данных: интерфейс и логика готовы, реальный сбор включается после согласования.'],
        ]} />
    </>
  )
}
