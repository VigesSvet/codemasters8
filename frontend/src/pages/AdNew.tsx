import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AiBadge, HypothesisItem, TermList } from '../components/Hypothesis'
import { Cell, Highlight, Modal, PageTitle, PlatformIcon, Score, ZoneTag, adKey, platformMeta } from '../components/ui'
import type { Ad, Analysis, AnalyzeInput, LinkHypothesis, OcrResult, OcrStatus, Platform } from '../data/types'
import { OCR_EMPTY, OCR_KEPT, ocrNotice, ocrOnEdit, ocrOnNewShot, ocrOnResult, pct, type OcrField } from '../lib/ai'
import { errorText } from '../lib/api'
import { finder, rankComplex } from '../lib/find'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCheck, IcChevronDown, IcClose, IcSearch, IcUpload } from '../ui/icons'

const SAMPLE = 'Старт продаж в новом ЖК «Тихий берег»! Квартиры у набережной, бронь от 300 000 ₸, первый взнос 15%, рассрочка от застройщика. Сдача 2027. Продаётся по договору долевого участия.'
/** Пауза после ввода перед анализом */
const DEBOUNCE = 400
const MAX_FILE = 10 * 1024 * 1024

/** Как выбран ЖК: auto — лучшая гипотеза ИИ (при сохранении её выберет сервер), hyp — гипотеза выбрана из списка,
 *  manual — аналитик выбрал ЖК сам. Гипотеза остаётся гипотезой; фактом привязку делает только выбор или подтверждение аналитика */
type Pick = { by: 'auto' } | { by: 'hyp'; h: LinkHypothesis } | { by: 'manual'; complexId: string }
/** Результат анализа для набора входных данных key; при ошибке остаётся прошлый результат */
type AiResult = { key: string; a: Analysis | null; err?: string }
/** id и время новой публикации — в момент сохранения */
const newId = () => ({ id: `m${Date.now().toString(36)}`, now: new Date().toISOString() })
const sec = (ms: number) => `${(ms / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} с`

/** Ручное добавление — раскладка панели заказа такси: поля «Откуда/Куда», плитки тарифов, жёлтая кнопка внизу */
export default function AdNew() {
  const { mode, complexes, developers, addAd, notify, analyze, ocr: recognize, ocrStatus, user } = useStore()
  const nav = useNavigate()
  const file = useRef<HTMLInputElement>(null)
  const ocrSeq = useRef(0)
  const [platform, setPlatform] = useState<Platform>('instagram')
  const [account, setAccount] = useState('')
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [img, setImg] = useState<string | undefined>()
  // поле «Текст на изображении» относится к текущему скриншоту: что положил OCR, что ввели вручную (см. lib/ai)
  const [ocrField, setOcrField] = useState<OcrField>(OCR_EMPTY)
  const ocrText = ocrField.text
  const [ocrRes, setOcrRes] = useState<OcrResult | null>(null)
  const [ocrBusy, setOcrBusy] = useState(false)
  const [ocrErr, setOcrErr] = useState<string | null>(null)
  const [ocrInfo, setOcrInfo] = useState<OcrStatus | null>(null)
  const [res, setRes] = useState<AiResult | null>(null)
  const [pick, setPick] = useState<Pick>({ by: 'auto' })
  const [over, setOver] = useState(false)
  const [modal, setModal] = useState(false)
  const [cq, setCq] = useState('')
  const [saving, setSaving] = useState(false)
  const live = mode === 'api'
  const demo = mode === 'demo'

  // OCR на сервере: доступен ли Tesseract и с какими языками — до загрузки скриншота
  useEffect(() => {
    if (!live) return
    let alive = true
    ocrStatus().then(
      (s) => { if (alive) setOcrInfo(s) },
      (e: unknown) => { if (alive) setOcrInfo({ available: false, engine: 'none', languages: [], detail: errorText(e) }) },
    )
    return () => { alive = false }
  }, [live]) // eslint-disable-line react-hooks/exhaustive-deps

  // Живой анализ: текст, распознанный скриншот, аккаунт, площадка и ссылка → POST /api/analyze через паузу после ввода
  const input = useMemo<AnalyzeInput>(() => ({
    text: text.trim(), ocr: ocrText.trim() || undefined, account: account.trim() || undefined, platform, url: url.trim() || undefined,
  }), [text, ocrText, account, platform, url])
  const key = JSON.stringify(input)
  const has = !!(input.text || input.ocr)
  useEffect(() => {
    if (!live || !has) return
    let alive = true
    const t = setTimeout(() => {
      analyze(input).then(
        (a) => { if (alive) setRes({ key, a }) },
        (e: unknown) => { if (alive) setRes((r) => ({ key, a: r?.a ?? null, err: errorText(e) })) },
      )
    }, DEBOUNCE)
    return () => { alive = false; clearTimeout(t) }
  }, [live, key]) // eslint-disable-line react-hooks/exhaustive-deps
  const ai = has ? res?.a ?? null : null
  const aiBusy = live && has && res?.key !== key
  const aiErr = has && res?.key === key ? res.err : undefined
  const hyps = ai?.hypotheses ?? []
  // без выбора сервер привяжет лучшую гипотезу не ниже порога низкой уверенности — и тоже как гипотезу
  const auto = hyps.find((h) => !h.low)
  const chosen = pick.by === 'hyp' ? hyps.find((h) => h.complexId === pick.h.complexId) ?? pick.h : undefined

  const target = pick.by === 'manual' ? { id: pick.complexId, conf: 1 } : chosen ? { id: chosen.complexId, conf: chosen.confidence }
    : auto ? { id: auto.complexId, conf: auto.confidence } : undefined
  const linked = target && complexes.find((c) => c.id === target.id)
  const manual = pick.by === 'manual'
  // id и дата — в момент сохранения; на оценку черновика они не влияют. Выбор вручную — привязка подтверждена этим аналитиком
  const draft: Ad = {
    id: '', platform, account: account || '@unknown', url, date: '', text, ocr: ocrText.trim() || undefined, image: img,
    complexId: linked?.id, matchConfidence: linked ? target?.conf ?? 0 : 0, linkConfirmedBy: manual ? user?.name ?? null : null,
    source: 'manual', status: 'new',
  }
  const s = scoreAd(draft, linked)

  // открываем разбор только после сохранения: при ошибке форма остаётся заполненной, причина — в баннере.
  // Пока распознаётся скриншот, не сохраняем: его текст (часто единственное место с условиями оплаты) пропал бы молча
  const save = () => {
    if (!text || saving || ocrBusy) return
    const { id, now } = newId()
    // «автоматически» — ЖК выбирает сервер тем же движком по сохраняемому тексту
    const ad: Ad = pick.by === 'auto' && live
      ? { ...draft, id, date: now, complexId: undefined, matchConfidence: 0 }
      : { ...draft, id, date: now, linkConfirmedAt: manual ? now : null }
    setSaving(true)
    addAd(ad).then((ok) => {
      setSaving(false)
      if (!ok) return
      notify(`Публикация ${adKey(ad.id)} добавлена в ленту`)
      nav(`/ads/${ad.id}`)
    })
  }

  const onFile = (f?: File) => {
    if (!f) return
    setOcrErr(null)
    if (f.size > MAX_FILE) { setOcrErr('Файл больше 10 МБ — уменьшите скриншот или обрежьте лишнее'); return }
    // новый скриншот заменяет прежний вместе с распознанным с него текстом (введённый вручную остаётся с пометкой).
    // Не изображение по типу файла — прежний скриншот остаётся, пока сервер не распознает новый (обычно 415)
    const replace = () => {
      setImg(URL.createObjectURL(f))
      setOcrRes(null)
      setOcrField(ocrOnNewShot)
    }
    const image = f.type.startsWith('image/')
    if (image) replace()
    if (!live) return
    const n = ++ocrSeq.current
    setOcrBusy(true)
    recognize(f).then(
      (r) => {
        if (ocrSeq.current !== n) return
        if (!image) replace()
        setOcrRes(r)
        setOcrField((x) => ocrOnResult(x, r))
      },
      (e: unknown) => { if (ocrSeq.current === n) setOcrErr(errorText(e)) },
    ).finally(() => { if (ocrSeq.current === n) setOcrBusy(false) })
  }
  /** Не ждать распознавания: ответ сервера будет отброшен, текст со скриншота можно ввести вручную */
  const cancelOcr = () => {
    ++ocrSeq.current
    setOcrBusy(false)
    setOcrErr('распознавание отменено — если на скриншоте есть текст, введите его вручную')
  }

  const ocrMsg = ocrNotice(ocrRes, ocrInfo?.detail, ocrField.kept)
  const pickNote = manual ? 'ЖК выбран вами — привязка сохранится как подтверждённая (факт) с вашим именем и попадёт в журнал.'
    : chosen ? `Выбрана гипотеза ИИ: ЖК «${chosen.complexName}», ${pct(chosen.confidence)}. Это ещё не факт — после сохранения примите её в разборе публикации, тогда объект получит баллы R2/R3.`
      : auto ? `Без выбора публикация будет привязана к лучшей гипотезе ИИ — ЖК «${auto.complexName}», ${pct(auto.confidence)} — как к гипотезе: в балл объекта она не войдёт, пока аналитик её не примет.`
        : live && ai ? 'ИИ не нашёл ЖК с достаточной уверенностью: публикация сохранится без привязки, если не выбрать ЖК вручную.'
          : 'Выберите ЖК вручную — тогда привязка сразу станет фактом.'

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Лента публикаций', '/ads'], ['Новая публикация']]} title="Новая публикация"
        sub="Обязательный источник по ТЗ: аналитик добавляет найденную рекламу вручную. ИИ распознаёт скриншот, находит условия оплаты и предлагает ЖК — решение за аналитиком." />

      <div className="cols-r" style={{ gridTemplateColumns: 'minmax(0, 1fr) 420px', gap: 48 }}>
        <div>
          <div className="fsection" style={{ marginTop: 0 }}>Публикация</div>
          <div className="frow top">
            <label>Площадка</label>
            <div className="chips">{(Object.keys(platformMeta) as Platform[]).map((p) => (
              <button key={p} className={`chip ${platform === p ? 'on' : ''}`} onClick={() => setPlatform(p)}><PlatformIcon p={p} size={18} />{platformMeta[p].name}</button>
            ))}</div>
          </div>
          <div className="frow"><label htmlFor="acc">Аккаунт</label><input id="acc" className="input" style={{ maxWidth: 420 }} placeholder="@account" value={account} onChange={(e) => setAccount(e.target.value)} /></div>
          <div className="frow"><label htmlFor="url">Ссылка</label><input id="url" className="input" placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} /></div>
          <div className="frow top">
            <label htmlFor="txt">Текст объявления</label>
            <div>
              <textarea id="txt" className="input" placeholder="Текст поста, описание видео или объявления" value={text} onChange={(e) => setText(e.target.value)} />
              {!text && <button className="link t-sm" style={{ marginTop: 6 }} onClick={() => setText(SAMPLE)}>Вставить пример</button>}
            </div>
          </div>

          <div className="fsection">Скриншот</div>
          <div className="frow top">
            <label>Изображение</label>
            <div>
              <div className={`drop ${over ? 'over' : ''}`} onClick={() => file.current?.click()} onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={(e) => { e.preventDefault(); setOver(false); onFile(e.dataTransfer.files[0]) }}>
                {img ? <img src={img} alt="" style={{ maxHeight: 220, borderRadius: 12 }} /> : (
                  <div><IcUpload /><div className="bold" style={{ marginTop: 8 }}>Перетащите файл или нажмите</div><div className="t-sm c2" style={{ marginTop: 4 }}>PNG, JPG до 10 МБ · текст на изображении распознаётся на сервере</div></div>
                )}
              </div>
              <input ref={file} type="file" accept="image/*" hidden onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = '' }} />
              <div className="ocr-meta">
                {demo ? <span>Демо-режим без бэкенда: OCR выполняется на сервере — перепечатайте текст со скриншота вручную</span>
                  : !live ? <span>Подключаемся к серверу…</span>
                    : !ocrInfo ? <span>Проверяем OCR на сервере…</span>
                      : ocrInfo.available ? <span>OCR: Tesseract{ocrInfo.version ? ` ${ocrInfo.version}` : ''} · языки {ocrInfo.languages.join(', ') || '—'} · офлайн, без внешних сервисов{ocrInfo.detail ? ` · ${ocrInfo.detail}` : ''}</span>
                        : <span>OCR недоступен: {ocrInfo.detail || 'Tesseract на сервере не работает — текст со скриншота введите вручную'}</span>}
              </div>
              {ocrBusy && (
                <div className="ai-note" style={{ marginTop: 10 }}>Распознаём текст на сервере… Сохранить публикацию можно будет после распознавания.{' '}
                  <button className="link" onClick={cancelOcr}>Не ждать</button>
                </div>
              )}
              {ocrErr && (
                <div className="ai-note err" style={{ marginTop: 10 }}>Не удалось распознать: {ocrErr}
                  {ocrField.kept && <div style={{ marginTop: 4 }}>{OCR_KEPT}</div>}
                </div>
              )}
              {!ocrBusy && ocrMsg && (
                <div className="ai-note warn" style={{ marginTop: 10 }}>{ocrMsg.text}
                  {ocrMsg.extra && <div className="t-xs" style={{ marginTop: 4, opacity: 0.8 }}>{ocrMsg.extra}</div>}
                </div>
              )}
              {ocrField.kept && !ocrBusy && !ocrErr && !ocrMsg && <div className="ai-note warn" style={{ marginTop: 10 }}>{OCR_KEPT}</div>}
            </div>
          </div>
          <div className="frow top">
            <label htmlFor="ocr">Текст на изображении</label>
            <div className="ocr-box" style={{ marginTop: 0 }}>
              <textarea id="ocr" className="input" placeholder={live ? 'Распознанный текст появится здесь — его можно поправить' : 'Перепечатайте текст со скриншота'} value={ocrText} onChange={(e) => { const v = e.target.value; setOcrField((x) => ocrOnEdit(x, v)) }} />
              {ocrRes && ocrRes.engine !== 'none' && ocrRes.text.trim() && (
                <div className="ocr-meta">
                  <span>Распознано: Tesseract{ocrRes.version ? ` ${ocrRes.version}` : ''}</span>
                  {ocrRes.confidence != null && <span>уверенность {pct(ocrRes.confidence)}</span>}
                  <span>{sec(ocrRes.durationMs)}</span>
                  {ocrRes.languages.length > 0 && <span>языки {ocrRes.languages.join(', ')}</span>}
                  {!!ocrRes.preprocess?.length && <span>предобработка: {ocrRes.preprocess.join(', ')}</span>}
                  <span>текст можно поправить — анализ пересчитается</span>
                </div>
              )}
            </div>
          </div>

          <div className="fsection row" style={{ gap: 10 }}>Анализ ИИ <AiBadge label="гипотезы ИИ" /></div>
          {demo ? (
            <div className="ai-note">Анализ ИИ выполняется на сервере: офлайн-движок без внешних API находит условия оплаты с цитатами и предлагает ЖК. В демо-режиме без бэкенда его нет — выберите ЖК вручную; оценка справа считается локально той же моделью риска.</div>
          ) : !live ? <div className="ai-note">Подключаемся к серверу…</div>
            : !has ? <div className="ai-note">Введите текст объявления или загрузите скриншот — ИИ найдёт условия оплаты и предложит ЖК. Это гипотезы: решение принимает аналитик.</div>
              : (
                <div style={{ opacity: aiBusy && ai ? 0.6 : 1, transition: 'opacity .15s' }}>
                  {aiErr && <div className="ai-note err" style={{ marginBottom: 8 }}>Анализ не выполнен: {aiErr}</div>}
                  {!ai ? (!aiErr && <div className="ai-note">Анализируем…</div>) : (
                    <>
                      <div className="frow top">
                        <label>Условия оплаты</label>
                        <TermList terms={ai.terms} empty="Условий оплаты ИИ не нашёл" />
                      </div>
                      {(ai.phones.length > 0 || ai.addresses.length > 0) && (
                        <div className="frow top">
                          <label>Найдено в тексте</label>
                          <div className="row wrap-f" style={{ gap: 6, paddingTop: 8 }}>
                            {ai.phones.map((p) => <span key={p} className="tag gray">тел. {p}</span>)}
                            {ai.addresses.map((a) => <span key={a} className="tag gray">{a}</span>)}
                          </div>
                        </div>
                      )}
                      {ai.note && <div className="ai-note" style={{ marginTop: 8 }}>{ai.note}</div>}
                      <div className="t-xs c3" style={{ marginTop: 8 }}>Движок {ai.engine} · правила, словари и нечёткое сравнение названий, без внешних API{aiBusy ? ' · обновляем…' : ''}</div>
                    </>
                  )}
                </div>
              )}

          <div className="fsection">Объект</div>
          <div className="frow top">
            <label>Жилой комплекс</label>
            <div>
              {live && hyps.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div className="t-sm c2" style={{ marginBottom: 8 }}>Гипотезы ИИ — нажмите, чтобы выбрать</div>
                  {hyps.map((h) => (
                    <HypothesisItem key={h.complexId} h={h} selected={pick.by === 'hyp' && pick.h.complexId === h.complexId}
                      onPick={() => setPick(pick.by === 'hyp' && pick.h.complexId === h.complexId ? { by: 'auto' } : { by: 'hyp', h })}
                      note={h === auto && pick.by === 'auto' ? 'будет выбрана, если не указать другую' : undefined} />
                  ))}
                </div>
              )}
              <button className="input row between" style={{ textAlign: 'left' }} onClick={() => setModal(true)}>
                <span>{manual && linked ? `ЖК «${linked.name}»` : <span className="c2">Выбрать ЖК вручную</span>}</span>
                <span className="row c2 t-sm" style={{ gap: 6 }}>{manual ? 'выбран вами — факт' : ''}<IcChevronDown size={16} /></span>
              </button>
              <p className="t-xs c2" style={{ marginTop: 8, lineHeight: '17px' }}>{pickNote}</p>
            </div>
          </div>
        </div>

        <aside className="sticky">
          <div className="box" style={{ padding: 24 }}>
            <div className="row between"><span className="t-h3">Предпросмотр</span><Score value={s.score} zone={s.zone} /></div>
            <div className="row" style={{ gap: 8, marginTop: 14 }}><PlatformIcon p={platform} size={22} /><span className="bold">{account || '@account'}</span><span style={{ marginLeft: 'auto' }}><ZoneTag zone={s.zone} /></span></div>
            {img && <div className="ph" style={{ height: 200, marginTop: 14, borderRadius: 12 }}><img src={img} alt="" /></div>}
            <p style={{ fontSize: 16, lineHeight: '23px', minHeight: 96, marginTop: 14 }}>{text ? <Highlight text={text} /> : <span className="c3">Текст появится здесь с подсветкой ключевых формулировок</span>}</p>
            <div className="kv" style={{ gridTemplateColumns: '1fr auto', marginTop: 16 }}>
              <span>Признаки продажи</span><span className="bold num">{s.signals.sale.length}</span>
              <span>Признаки сбора денег</span><span className="bold num" style={{ color: 'var(--red)' }}>{s.signals.funds.length}</span>
              <span>Признаки законности</span><span className="bold num" style={{ color: 'var(--green)' }}>{s.signals.safe.length}</span>
              {ai && <><span>Условия оплаты (ИИ)</span><span className="bold num">{ai.terms.length}</span></>}
              <span>Объект</span><span style={{ textAlign: 'right' }}>{linked ? <>{linked.name}<div className="t-xs c2">{manual ? 'выбран вами — факт' : `гипотеза ИИ ${pct(target?.conf ?? 0)}`}</div></> : '—'}</span>
            </div>
            <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>R2 и R3 — по словарю модели, R1 объекта — только при подтверждённой привязке. Гипотеза ИИ в балл не входит.</p>
            <button className="btn y full" style={{ marginTop: 16 }} disabled={!text || saving || ocrBusy} onClick={save}>
              {saving ? 'Сохраняем…' : ocrBusy ? 'Распознаём скриншот…' : text ? 'Сохранить и оценить' : 'Введите текст объявления'}
            </button>
          </div>
        </aside>
      </div>

      {modal && (
        <Modal title="Выберите объект" onClose={() => setModal(false)}>
          <div style={{ padding: '8px 24px' }}><div className="search"><IcSearch size={20} /><input autoFocus placeholder="Название, адрес или застройщик" value={cq} onChange={(e) => setCq(e.target.value)} />{cq && <button onClick={() => setCq('')}><IcClose size={18} /></button>}</div></div>
          <Cell title={live ? 'По гипотезе ИИ' : 'Без привязки'} sub={live ? 'ЖК выберет ИИ при сохранении — как гипотезу' : 'Публикацию можно привязать позже в разборе'}
            end={!manual ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined} onClick={() => { setPick({ by: 'auto' }); setModal(false) }} />
          {complexes.filter(finder(cq, complexes, (q, x) => rankComplex(q, x, developers.find((d) => d.id === x.developerId)))).map((x) => (
            <Cell key={x.id} inset icon={<img className="th" src={x.cover} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />} title={x.name} sub={x.address}
              end={manual && pick.complexId === x.id ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined}
              onClick={() => { setPick({ by: 'manual', complexId: x.id }); setModal(false) }} />
          ))}
        </Modal>
      )}
    </>
  )
}
