import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Origin, SrcLink } from '../components/Provenance'
import { Faq, Hero, HeroTile, PlatformIcon, platformMeta } from '../components/ui'
import { publishedListMeta } from '../data/demo'
import { OFFLINE_FILE } from '../data/openData'
import type { ImportReport, Platform, SourceInfo } from '../data/types'
import { errorText } from '../lib/api'
import { OPEN_DATA_NOTE, isDemoAd, sourceName, when } from '../lib/provenance'
import { useStore } from '../lib/store'
import { IcClock, IcDatabase, IcDoc, IcInfo, IcMegaphone, IcPencil, IcShield, IcUpload } from '../ui/icons'

const MAX_MB = 5
const ACCEPT = '.xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv'
const PLATFORMS: Platform[] = ['instagram', 'tiktok', 'telegram', 'krisha', 'olx', '2gis']
const gray = (n: ReactNode) => <span className="zone-pic gray" style={{ width: 48, height: 48, borderRadius: 14, flex: 'none' }}>{n}</span>
const records = (n: number) => `${n.toLocaleString('ru-RU')} ${n % 10 === 1 && n % 100 !== 11 ? 'запись' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'записи' : 'записей'}`
const latest = (xs: (string | null | undefined)[]) => xs.filter((x): x is string => !!x).sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0]

type Imp = { file?: File; busy?: boolean; report?: ImportReport; err?: string }

/** Источники — что реально есть: открытые данные (импорт файла), ручной ввод, демо-набор. Без имитации интеграций (ТЗ §4) */
export default function Sources() {
  const { mode, ads, complexes, developers, appeals, sources, importSource, notify, revision } = useStore()
  const live = mode === 'api'
  const [load, setLoad] = useState<{ data?: SourceInfo[]; err?: string } | null>(null)
  const [ver, setVer] = useState(0)
  const [imp, setImp] = useState<Imp>({})
  const input = useRef<HTMLInputElement>(null)

  // после импорта (revision) и восстановления связи — перечитываем
  useEffect(() => {
    if (mode === 'loading') return
    let alive = true
    sources().then((data) => { if (alive) setLoad({ data }) }, (e: unknown) => { if (alive) setLoad((l) => ({ data: l?.data, err: errorText(e) })) })
    return () => { alive = false }
  }, [mode, ver, revision]) // eslint-disable-line react-hooks/exhaustive-deps

  const open = (load?.data ?? []).filter((s) => s.code !== 'demo')
  const total = open.reduce((n, s) => n + (s.records || 0), 0)
  const last = latest(open.map((s) => s.collectedAt))
  const demoAds = ads.filter(isDemoAd)
  const manual = ads.filter((a) => !isDemoAd(a) && (a.origin === 'analyst' || a.source === 'manual')).length
  const byPlat = (p: Platform) => demoAds.filter((a) => a.platform === p).length

  const pick = (f?: File) => setImp(f ? { file: f, err: f.size > MAX_MB * 1024 * 1024 ? `Файл больше ${MAX_MB} МБ` : undefined } : {})
  const upload = () => {
    const f = imp.file
    if (!f || imp.busy || imp.err) return
    setImp({ file: f, busy: true })
    importSource(f).then(
      (report) => {
        setImp({ report })
        if (input.current) input.current.value = ''
        notify(`Импорт: ${report.created} новых, ${report.updated} обновлено${report.unchanged != null ? `, ${report.unchanged} без изменений` : ''}, ${report.skipped} пропущено — событие в журнале`)
      },
      // ошибка файла (формат, размер, шапка) — здесь; прочие — ещё и в баннере
      (e: unknown) => setImp({ file: f, err: errorText(e) }),
    )
  }

  return (
    <>
      <Hero crumb="Источники данных" title="Откуда берутся данные"
        info="Без закрытых государственных API и без имитации интеграций: открытые данные загружаются файлом, публикации — вручную. У каждой записи — источник, ссылка и время сбора"
        tiles={<>
          <HeroTile icon={<IcDatabase size={32} />} label="Записей открытых данных" value={load?.data ? total : '—'} to="/complexes?layer=open" />
          <HeroTile icon={<IcDoc size={32} />} label="Открытых источников" value={load?.data ? open.length : '—'} onClick={() => document.getElementById('open')?.scrollIntoView({ behavior: 'smooth' })} />
          <HeroTile icon={<IcClock size={32} />} label="Последний сбор" value={last ? when(last).split(',')[0] : '—'} onClick={() => document.getElementById('open')?.scrollIntoView({ behavior: 'smooth' })} />
          <HeroTile icon={<IcMegaphone size={32} />} label="Публикаций в ленте" value={ads.length} tone="blue" to="/ads" />
          <HeroTile icon={<IcInfo size={32} />} label="Демо-набор, ЖК" value={complexes.length} to="/complexes" />
        </>} />

      <div className="box warn t-sm" style={{ marginBottom: 32, lineHeight: '20px' }}>
        <b>Автоматической синхронизации с реестрами нет.</b> Записи Homeportal и Казреестра (CSV Ashyq Data) собраны в файл и загружены импортом —
        у каждой сохранены ссылка, ID в источнике и время сбора. Чтобы обновить данные, загрузите новый файл ниже. Сбора из соцсетей и площадок объявлений тоже нет.
      </div>

      <h2 className="t-h2" id="open" style={{ marginBottom: 4 }}>Открытые данные</h2>
      <p className="c2" style={{ marginBottom: 16, maxWidth: 820 }}>{OPEN_DATA_NOTE} <Link className="link" to="/complexes?layer=open">Объекты в реестре</Link></p>
      {mode === 'demo' && <div className="ai-note" style={{ marginBottom: 16 }}>Демо-режим без бэкенда: показана встроенная копия открытых данных ({OFFLINE_FILE}) — те же записи, что загружает сервер. Импорт новых файлов — с бэкендом (./dev.sh).</div>}
      {mode === 'loading' ? <div className="ai-note">Подключаемся к серверу…</div>
        : !load ? <div className="ai-note">Загружаем список источников…</div>
          : !load.data ? <div className="ai-note err">Список источников не загрузился: {load.err} <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></div>
            : open.length === 0 ? <div className="ai-note">Открытых данных пока нет — загрузите файл ниже.</div>
              : (
                <div className="gcards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 16 }}>
                  {open.map((s) => (
                    <div key={s.code} className="devcard" style={{ cursor: 'default' }}>
                      <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
                        {gray(<IcDatabase size={22} />)}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="t-h3" style={{ fontSize: 18 }}>{s.name || sourceName(s.code)}</div>
                          <div className="t-sm c2" style={{ marginTop: 2 }}>{records(s.records)}{s.objects != null && ` · объектов: ${s.objects}`}</div>
                        </div>
                        <span className="tag gray">{live ? 'импорт файла' : 'встроенная копия'}</span>
                      </div>
                      <div className="kv t-sm" style={{ gridTemplateColumns: '170px minmax(0, 1fr)', gap: '6px 12px' }}>
                        <span>Когда собрано</span><span>{s.collectedAt ? <span title={s.collectedAt}>{when(s.collectedAt)}</span> : '—'}</span>
                        {s.providerLoadedAt && <><span>Загружено у поставщика</span><span title={s.providerLoadedAt}>{when(s.providerLoadedAt)}</span></>}
                        {s.url && <><span>Ссылка</span><span className="long-url"><SrcLink url={s.url}>{s.url.replace(/^https?:\/\//, '')}</SrcLink></span></>}
                      </div>
                      {s.note && <div className="t-sm c2" style={{ lineHeight: '19px' }}>{s.note}</div>}
                    </div>
                  ))}
                </div>
              )}
      {load?.err && load.data && <div className="alog-err t-sm" style={{ marginTop: 8 }}>Список не обновился: {load.err} <button className="link" onClick={() => setVer((v) => v + 1)}>Повторить</button></div>}

      <section className="card" style={{ marginTop: 24 }}>
        <div className="card-head"><h3>Импорт файла открытых данных</h3><span className="t-sm c2">xlsx или csv, до {MAX_MB} МБ</span></div>
        <p className="t-sm c2" style={{ lineHeight: '20px', maxWidth: 820 }}>
          Формат — как <code>docs/Усть-Каменогорск_объекты.xlsx</code>: шапка из 26 колонок (источник, ЖК, адрес, застройщик и БИН, способ привлечения средств,
          документы, координаты, ссылка на источник, когда собраны данные, ID в источнике, примечание). CSV — разделитель «,» или «;», UTF-8.
          Записи сверяются по паре «источник + ID в источнике»: известные обновляются, новые добавляются — повторная загрузка того же файла новых не даёт.
          Импорт пишется в журнал с автором и временем.
        </p>
        <div className="imp-form" style={{ marginTop: 16 }}>
          <label className={`imp-file ${!live ? 'c3' : ''}`} aria-disabled={!live}>
            <IcUpload size={18} />{imp.file ? imp.file.name : 'Выбрать файл'}
            <input ref={input} type="file" accept={ACCEPT} disabled={!live || imp.busy} onChange={(e) => pick(e.target.files?.[0])} />
          </label>
          <button className="btn y" disabled={!live || !imp.file || !!imp.err || imp.busy} onClick={upload}>{imp.busy ? 'Загружаем…' : 'Импортировать'}</button>
          {imp.file && !imp.busy && <span className="t-sm c2">{(imp.file.size / 1024).toLocaleString('ru-RU', { maximumFractionDigits: 0 })} КБ</span>}
        </div>
        {!live && <p className="t-sm c2" style={{ marginTop: 10 }}>{mode === 'loading' ? 'Подключаемся к серверу…' : 'Импорт выполняется на сервере — в демо-режиме без бэкенда недоступен (запустите ./dev.sh).'}</p>}
        {imp.err && <div className="alog-err t-sm" style={{ marginTop: 12 }}>Не импортировано: {imp.err}</div>}
        {imp.report && (
          <>
            <div className="imp-report">
              <div><b>{imp.report.read}</b><span>строк прочитано</span></div>
              <div><b>{imp.report.created}</b><span>новых записей</span></div>
              <div><b>{imp.report.updated}</b><span>обновлено</span></div>
              {imp.report.unchanged != null && <div><b>{imp.report.unchanged}</b><span>без изменений</span></div>}
              <div><b>{imp.report.skipped}</b><span>пропущено</span></div>
              {imp.report.duplicates != null && <div><b>{imp.report.duplicates}</b><span>повторов в файле</span></div>}
            </div>
            <p className="t-sm c2" style={{ marginTop: 10 }}>
              {imp.report.file && <>Файл «{imp.report.file}». </>}
              {imp.report.created === 0 && imp.report.updated + (imp.report.unchanged ?? 0) > 0 ? 'Новых записей нет: все строки уже были в системе и сверены по паре «источник + ID в источнике». ' : ''}
              {imp.report.objects != null && <>В реестре: {imp.report.records ?? '—'} записей, {imp.report.objects} объектов{imp.report.withoutCoords != null && `, без координат ${imp.report.withoutCoords}`}. </>}
              <Link className="link" to="/complexes?layer=open">Открыть реестр открытых данных</Link>
            </p>
            {[...(imp.report.errors ?? []), ...(imp.report.warnings ?? [])].length > 0 && (() => {
              const xs = [...(imp.report.errors ?? []), ...(imp.report.warnings ?? [])]
              return (
                <ul className="t-sm c2" style={{ margin: '8px 0 0', paddingLeft: 18, lineHeight: '19px' }}>
                  {xs.slice(0, 10).map((x, i) => <li key={i}>{x}</li>)}
                  {xs.length > 10 && <li>…и ещё {xs.length - 10}</li>}
                </ul>
              )
            })()}
          </>
        )}
      </section>

      <h2 className="t-h2" style={{ margin: '48px 0 16px' }}>Публикации и демо-набор</h2>
      <div className="gcards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 16 }}>
        <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcPencil size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Ручной ввод публикаций</div><div className="t-sm c2" style={{ marginTop: 2 }}>Обязательный уровень ТЗ</div></div>
            <span className="tag green">работает</span>
          </div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>Аналитик вставляет ссылку, текст и скриншот: текст со скриншота распознаётся на сервере (OCR), ИИ предлагает ЖК и находит условия оплаты.</div>
          <div className="row between"><span className="t-sm c2">Добавлено вручную: {manual}</span><Link to="/ads/new" className="link t-sm bold">Добавить публикацию</Link></div>
        </div>
        <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcInfo size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Демонстрационный набор</div><div className="t-sm c2" style={{ marginTop: 2 }}>shared/seed_data.json</div></div>
            <Origin kind="demo" label="демо" />
          </div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>Вымышленные ЖК, застройщики, публикации и обращения — на них показаны модель риска и сценарии A, B, C (ТЗ §5). Во всём интерфейсе помечены «демо».</div>
          <div className="t-sm c2">{complexes.length} ЖК · {developers.length} застройщиков · {demoAds.length} публикаций · {appeals.length} обращений</div>
        </div>
        <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcShield size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Перечень ГАСК</div><div className="t-sm c2" style={{ marginTop: 2 }}>Разрешённые ЖК · наблюдение O1, вне балла</div></div>
            <Origin kind="demo" label="демо" />
          </div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>Список объектов из Instagram ГАСК ВКО, с которым сверяются ЖК демо-набора. Отметка в карточке ЖК — с источником и датой перечня.</div>
          <div className="t-sm c2">{publishedListMeta.count} ЖК · {publishedListMeta.source} · обновлён {when(publishedListMeta.updated)}</div>
        </div>
        <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcMegaphone size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Соцсети и площадки объявлений</div><div className="t-sm c2" style={{ marginTop: 2 }}>Автоматический сбор</div></div>
            <span className="tag gray">не подключены</span>
          </div>
          <div className="row" style={{ gap: 6 }}>{PLATFORMS.map((p) => <PlatformIcon key={p} p={p} size={28} />)}</div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>Автоматического сбора нет. Публикации с этих площадок в ленте — демонстрационные, остальные аналитик добавляет вручную. Сбор возможен только при соблюдении правил площадки.</div>
          <div className="t-sm c2">В демо-наборе: {PLATFORMS.filter((p) => byPlat(p)).map((p) => `${platformMeta[p].name} ${byPlat(p)}`).join(' · ') || 'нет'}</div>
        </div>
      </div>

      <Faq title="Вопросы об источниках"
        items={[
          ['Есть ли автоматическая синхронизация с реестрами?', 'Нет. Записи Homeportal и Казреестра (CSV Ashyq Data) собраны в файл и загружены импортом; у каждой сохранены ссылка, ID в источнике, время сбора и дата загрузки у поставщика. Чтобы обновить — загрузите новый файл: известные записи обновятся, новые добавятся. Реестр с API можно подключить как ещё один источник, когда появится доступ.'],
          ['Почему у реальных объектов нет балла риска?', `${OPEN_DATA_NOTE} Демо не публикует обвинений в адрес реальных компаний (ТЗ §7): риск, зоны и сигналы строятся только на вымышленных ЖК демо-набора.`],
          ['Почему реклама попадает не вся?', 'Таргетированную рекламу соцсети показывают конкретным людям — публичным сбором её не увидеть. Поэтому главный канал — ручное добавление: аналитик или житель присылает скриншот.'],
          ['Нужен ли доступ к закрытым госреестрам?', 'Нет. Система работает на открытых данных, загруженных файлом, на ручном вводе и на демо-наборе.'],
          ['Как связывается публикация с ЖК?', 'ИИ сравнивает текст и распознанный скриншот с объектами: название (с транслитерацией и опечатками), адрес, аккаунт и телефон застройщика — и предлагает до трёх гипотез с причинами и уверенностью. Фактом привязка становится только после того, как аналитик примет гипотезу или выберет ЖК сам.'],
        ]} />
    </>
  )
}
