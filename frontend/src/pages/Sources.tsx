import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import Connectors, { CollectAllButton, CollectorsChain, ConnectorsProvider } from '../components/Connectors'
import { Origin, SrcLink } from '../components/Provenance'
import { Faq, Hero } from '../components/ui'
import { OFFLINE_FILE } from '../data/openData'
import { READ_ONLY, SNAPSHOT_DIR } from '../data/snapshot'
import type { ImportReport, SourceInfo } from '../data/types'
import { errorText } from '../lib/api'
import { OPEN_DATA_NOTE, REGISTRY_NOTE, isDemo, isDemoAd, isRegistry, sourceName, when } from '../lib/provenance'
import { useStore } from '../lib/store'
import { listChecked } from '../lib/verification'
import { IcDatabase, IcInfo, IcPencil, IcShield, IcUpload } from '../ui/icons'
import { Board, Chain, type Fact } from '../components/StatBoard'

const MAX_MB = 5
const ACCEPT = '.xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv'
const gray = (n: ReactNode) => <span className="zone-pic gray" style={{ width: 48, height: 48, borderRadius: 14, flex: 'none' }}>{n}</span>
const records = (n: number) => `${n.toLocaleString('ru-RU')} ${n % 10 === 1 && n % 100 !== 11 ? 'запись' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'записи' : 'записей'}`
const latest = (xs: (string | null | undefined)[]) => xs.filter((x): x is string => !!x).sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0]

type Imp = { file?: File; busy?: boolean; report?: ImportReport; err?: string }

/** Источники — что реально есть: открытые данные (импорт файла), подключения сбора публикаций (фактическое состояние с сервера),
 *  ручной ввод, стартовый набор (shared/snapshot). Без имитации интеграций (ТЗ §4) */
export default function Sources() {
  const { mode, snapshot, ads, complexes, developers, appeals, sources, importSource, notify, revision } = useStore()
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

  // демо-набор (старые БД) и офлайн-копия — не открытые источники: у них свои карточки ниже
  const open = (load?.data ?? []).filter((s) => s.code !== 'demo' && s.code !== 'snapshot')
  const snap = load?.data?.find((s) => s.code === 'snapshot')
  const total = open.reduce((n, s) => n + (s.records || 0), 0)
  const last = latest(open.map((s) => s.collectedAt))
  const demoAds = ads.filter(isDemoAd)
  // свежесть открытых данных по дате последнего сбора: до недели — свежие, до месяца — стареют, дальше — устарели
  const freshness = (d: string): Fact => {
    const days = Math.floor((Date.now() - new Date(d).getTime()) / 86400000)
    const text = `собрано ${when(d).split(',')[0]}`
    return { text, tone: days <= 7 ? 'green' : days <= 30 ? 'yellow' : 'red' }
  }
  // строки прежнего демо-набора — только в старых БД (до python -m app.demo purge); карточки ЖК из открытого реестра — всегда
  const demoCx = complexes.filter(isDemo), demoDevs = developers.filter(isDemo), demoAppeals = appeals.filter(isDemo)
  const regCx = complexes.filter(isRegistry), regDevs = developers.filter(isRegistry)
  const manual = ads.filter((a) => !isDemoAd(a) && (a.origin === 'analyst' || a.source === 'manual')).length
  const listN = complexes.filter(listChecked).length

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
    <ConnectorsProvider>
      <Hero crumb="Источники данных" title="Откуда берутся данные"
        info="Открытые данные загружаются файлом, публикации — подключёнными сборщиками, через бота заявок и вручную. У каждой записи — источник, ссылка и время сбора. Состояние подключений — фактическое, с сервера"
        actions={<CollectAllButton />}
        board={<Board kind="sources">
          <Chain n="1 · Открытые данные" em="database" value={load?.data ? total : '—'} label="записей реестров" to="/complexes?layer=open" next
            facts={load?.data ? [{ text: `${open.length} ${open.length === 1 ? 'источник' : open.length < 5 ? 'источника' : 'источников'}` }, ...(last ? [freshness(last)] : [])] : []} />
          <CollectorsChain />
          <Chain n="3 · Лента" em="feed" tone="blue" value={ads.length} label="публикаций в ленте" to="/ads" next
            facts={[{ text: `собрано автоматически — ${ads.filter((a) => a.origin === 'collected').length}` }]} />
          <Chain n="4 · Контроль" em="city" value={complexes.length} label="ЖК на контроле" to="/complexes"
            facts={[{ text: `из реестра — ${complexes.filter(isRegistry).length}` }]} />
        </Board>} />

      <div className="box warn t-sm" style={{ marginBottom: 32, lineHeight: '20px' }}>
        <b>Автоматической синхронизации с реестрами нет.</b> Записи Homeportal и Казреестра (CSV Ashyq Data) собраны в файл и загружены импортом —
        у каждой сохранены ссылка, ID в источнике и время сбора. Чтобы обновить данные, загрузите новый файл ниже.
        Сбор публикаций — в разделе <button className="link" onClick={() => document.getElementById('connectors')?.scrollIntoView({ behavior: 'smooth' })}>«Подключения»</button> ниже:
        показано только то, что реально настроено и проверено.
      </div>

      <Connectors />

      <h2 className="t-h2" id="open" style={{ marginBottom: 4 }}>Открытые данные</h2>
      <p className="c2" style={{ marginBottom: 16, maxWidth: 820 }}>{OPEN_DATA_NOTE} <Link className="link" to="/complexes?layer=open">Объекты в реестре</Link></p>
      {mode === 'snapshot' && <div className="ai-note" style={{ marginBottom: 16 }}>Офлайн-копия: показана встроенная копия открытых данных ({OFFLINE_FILE}) — те же записи, что загружает сервер. Импорт новых файлов — с сервером (./dev.sh).</div>}
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
                        <span className="tag gray">{live ? 'импорт файла' : 'офлайн-копия'}</span>
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
        {!live && <p className="t-sm c2" style={{ marginTop: 10 }}>{mode === 'loading' ? 'Подключаемся к серверу…' : 'Импорт выполняется на сервере — в офлайн-копии недоступен (запустите ./dev.sh).'}</p>}
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

      <h2 className="t-h2" style={{ margin: '48px 0 16px' }}>Публикации и стартовый набор</h2>
      <div className="gcards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 16 }}>
        <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcPencil size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Ручной ввод публикаций</div><div className="t-sm c2" style={{ marginTop: 2 }}>Обязательный уровень ТЗ</div></div>
            <span className="tag green">работает</span>
          </div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>Аналитик вставляет ссылку, текст и скриншот: текст со скриншота распознаётся на сервере (OCR), ИИ предлагает ЖК и находит условия оплаты.</div>
          <div className="row between"><span className="t-sm c2">Добавлено вручную: {manual}</span>{live ? <Link to="/ads/new" className="link t-sm bold">Добавить публикацию</Link> : <span className="t-sm c2">с сервером</span>}</div>
        </div>
        <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcDatabase size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Стартовый набор данных</div><div className="t-sm c2" style={{ marginTop: 2 }}>{SNAPSHOT_DIR}</div></div>
            <span className={`tag ${mode === 'snapshot' ? 'yellow' : 'gray'}`}>{mode === 'snapshot' ? 'офлайн-копия' : 'загружается в пустую БД'}</span>
          </div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>Реальные данные мониторинга: ЖК из открытого реестра и известные по рекламе, их застройщики, публичная реклама застройщиков и агентств
            (собрана 18–19.10.2026) с распознанным текстом и анализом ИИ, объекты из рекламы. Сервер загружает набор в пустую базу при первом запуске,
            дальше данные пополняют сборщики и аналитики. «Проверка по рекламе» — предварительная автоматическая оценка, не вывод о нарушении.</div>
          {mode === 'snapshot' && <div className="t-sm" style={{ lineHeight: '19px' }}>Сервер недоступен — открыта копия набора{snap?.collectedAt ? ` (выгружена ${when(snap.collectedAt)})` : ''}. {READ_ONLY}.</div>}
          {snapshot && <div className="t-sm c2">{snapshot.counts.complexes} ЖК · {snapshot.counts.developers} застройщиков · {snapshot.counts.ads} публикаций · {snapshot.counts.discovered} объектов из рекламы · {snapshot.counts.images} изображений</div>}
        </div>
        {(demoCx.length > 0 || demoAds.length > 0) && <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcInfo size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Прежний демонстрационный набор</div><div className="t-sm c2" style={{ marginTop: 2 }}>python -m app.demo purge — удалить</div></div>
            <Origin kind="demo" label="демо" />
          </div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>В базе остались вымышленные ЖК, застройщики, публикации и обращения прежнего демо-набора — в интерфейсе они помечены «демо». Новые развёртывания их не получают.</div>
          <div className="t-sm c2">{demoCx.length} ЖК · {demoDevs.length} застройщиков · {demoAds.length} публикаций · {demoAppeals.length} обращений</div>
        </div>}
        {regCx.length > 0 && <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcDatabase size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Карточки ЖК из открытого реестра</div><div className="t-sm c2" style={{ marginTop: 2 }}>python -m app.demo import-registry</div></div>
            <Origin kind="registry" />
          </div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>Объекты реестра с координатами стали карточками ЖК, к которым привязываются собранные публикации: {REGISTRY_NOTE}. Риск по карточке не оценивается, пока нет проверенных данных.</div>
          <div className="t-sm c2">{regCx.length} ЖК · {regDevs.length} застройщиков</div>
        </div>}
        <div className="devcard" style={{ cursor: 'default' }}>
          <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
            {gray(<IcShield size={22} />)}
            <div style={{ flex: 1 }}><div className="t-h3" style={{ fontSize: 18 }}>Перечень ГАСК</div><div className="t-sm c2" style={{ marginTop: 2 }}>Разрешённые ЖК · наблюдение O1, вне балла</div></div>
            <span className="tag gray">сверка вручную</span>
          </div>
          <div className="t-sm" style={{ lineHeight: '19px' }}>Список объектов из Instagram ГАСК ВКО. Файлом не загружается: аналитик сверяет ЖК с перечнем в карточке ЖК — отметка с источником, датой перечня и автором.</div>
          <div className="t-sm c2">Сверено с перечнем: {listN} ЖК из {complexes.length}</div>
        </div>
      </div>

      <Faq title="Вопросы об источниках"
        items={[
          ['Есть ли автоматическая синхронизация с реестрами?', 'Нет. Записи Homeportal и Казреестра (CSV Ashyq Data) собраны в файл и загружены импортом; у каждой сохранены ссылка, ID в источнике, время сбора и дата загрузки у поставщика. Чтобы обновить — загрузите новый файл: известные записи обновятся, новые добавятся. Реестр с API можно подключить как ещё один источник, когда появится доступ.'],
          ['Почему у реальных объектов нет балла риска?', `${OPEN_DATA_NOTE} Система не публикует обвинений в адрес реальных компаний по непроверенным сведениям (ТЗ §7): у карточки ЖК из открытого реестра балл, зона и сигнал появляются только после проверенных данных — подтверждённой аналитиком привязки публикации, сверенного документа, обращения или снимка площадки.`],
          ['Почему реклама попадает не вся?', 'Сборщики читают только публичные аккаунты и каналы из списка; таргетированную рекламу соцсети показывают конкретным людям — её присылают аналитики и жители (ручной ввод и Telegram-бот заявок).'],
          ['Нужен ли доступ к закрытым госреестрам?', 'Нет. Система работает на открытых данных, загруженных файлом, на публикациях подключённых источников (публичные аккаунты, каналы и выдача площадок) и на ручном вводе. Закрытые реестры и чужие аккаунты не нужны.'],
          ['Кто настраивает подключения?', 'Только администратор: токен SAQ_ADMIN_TOKEN он вводит в этой вкладке, проверяет его сервер. Ключи площадок и сервисов (Meta, Telegram, Apify, 2ГИС, OLX, Anthropic) задаются в .env на сервере и в браузер не передаются. Вход аналитика без пароля прав администратора не даёт.'],
          ['Почему 2ГИС, анализатор ИИ и OLX — не сборщики?', '2ГИС — геосервис: предлагает кандидатов адреса для карточки ЖК, решение принимает аналитик. Анализатор ИИ (Anthropic): читает текст и OCR уже собранных публикаций и предлагает гипотезы с дословными цитатами (признаки продажи, суммы, кандидаты ЖК); текст и OCR для этого отправляются в Anthropic, решение принимает аналитик. OLX Partner API даёт доступ только к ресурсам своего авторизованного аккаунта — чужие объявления так не собрать.'],
          ['Как связывается публикация с ЖК?', 'ИИ сравнивает текст и распознанный скриншот с объектами: название (с транслитерацией и опечатками), адрес, аккаунт и телефон застройщика — и предлагает до трёх гипотез с причинами и уверенностью. Фактом привязка становится только после того, как аналитик примет гипотезу или выберет ЖК сам.'],
        ]} />
    </ConnectorsProvider>
  )
}
