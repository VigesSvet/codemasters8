import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Cell, Highlight, Modal, PageTitle, PlatformIcon, Score, ZoneTag, platformMeta } from '../components/ui'
import type { Ad, Platform } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCheck, IcChevronDown, IcClose, IcSearch, IcUpload } from '../ui/icons'

const SAMPLE = 'Старт продаж в новом ЖК «Тихий берег»! Квартиры у набережной, бронь от 300 000 ₸, первый взнос 15%, рассрочка от застройщика. Сдача 2027. Продаётся по договору долевого участия.'

/** Ручное добавление — раскладка панели заказа такси: поля «Откуда/Куда», плитки тарифов, жёлтая кнопка внизу */
export default function AdNew() {
  const { complexes, addAd, notify } = useStore()
  const nav = useNavigate()
  const file = useRef<HTMLInputElement>(null)
  const [platform, setPlatform] = useState<Platform>('instagram')
  const [account, setAccount] = useState('')
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [img, setImg] = useState<string | undefined>()
  const [ocr, setOcr] = useState<string | undefined>()
  const [ocrBusy, setOcrBusy] = useState(false)
  const [complexId, setComplexId] = useState('')
  const [over, setOver] = useState(false)
  const [modal, setModal] = useState(false)
  const [cq, setCq] = useState('')

  const suggestion = useMemo(() => complexes.find((c) => `${text} ${ocr ?? ''}`.toLowerCase().includes(c.name.toLowerCase())), [text, ocr, complexes])
  const linked = complexes.find((c) => c.id === (complexId || suggestion?.id))
  const draft: Ad = { id: `m${Date.now().toString(36)}`, platform, account: account || '@unknown', url, date: new Date().toISOString(), text, ocr, image: img, complexId: linked?.id, matchConfidence: complexId ? 1 : suggestion ? 0.8 : 0, source: 'manual', status: 'new' }
  const s = scoreAd(draft, linked)

  const onFile = (f?: File) => {
    if (!f) return
    setImg(URL.createObjectURL(f))
    setOcrBusy(true)
    // В бэкенде: POST /api/ocr (Tesseract rus+kaz). Здесь — демо-результат.
    setTimeout(() => { setOcr('СТАРТ ПРОДАЖ · БРОНЬ · РАССРОЧКА 0% · СДАЧА 2027'); setOcrBusy(false) }, 1100)
  }

  return (
    <>
      <PageTitle crumbs={[['Карта', '/'], ['Лента публикаций', '/ads'], ['Новая публикация']]} title="Новая публикация"
        sub="Обязательный источник по ТЗ: аналитик добавляет найденную рекламу вручную. Система распознаёт текст, привязывает ЖК и сразу показывает оценку." />

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
                  <div><IcUpload /><div className="bold" style={{ marginTop: 8 }}>Перетащите файл или нажмите</div><div className="t-sm c2" style={{ marginTop: 4 }}>PNG, JPG до 10 МБ · текст на изображении распознается</div></div>
                )}
              </div>
              <input ref={file} type="file" accept="image/*" hidden onChange={(e) => onFile(e.target.files?.[0])} />
              {(ocrBusy || ocr) && <div className="t-sm" style={{ marginTop: 10 }}>{ocrBusy ? <span className="c2">Распознаём текст…</span> : <><span className="c2">Распознано: </span><Highlight text={ocr!} /></>}</div>}
            </div>
          </div>

          <div className="fsection">Объект</div>
          <div className="frow">
            <label>Жилой комплекс</label>
            <button className="input row between" style={{ textAlign: 'left' }} onClick={() => setModal(true)}>
              <span>{linked ? `ЖК «${linked.name}»` : <span className="c3">Определится по тексту</span>}</span>
              <span className="row c2 t-sm" style={{ gap: 6 }}>{complexId ? 'выбран вручную' : suggestion ? 'найден в тексте' : ''}<IcChevronDown size={16} /></span>
            </button>
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
              <span>Объект</span><span>{linked ? linked.name : '—'}</span>
            </div>
            <button className="btn y full" style={{ marginTop: 20 }} disabled={!text} onClick={() => { addAd(draft); notify('Публикация добавлена в ленту'); nav(`/ads/${draft.id}`) }}>{text ? 'Сохранить и оценить' : 'Введите текст объявления'}</button>
          </div>
        </aside>
      </div>

      {modal && (
        <Modal title="Выберите объект" onClose={() => setModal(false)}>
          <div style={{ padding: '8px 24px' }}><div className="search"><IcSearch size={20} /><input autoFocus placeholder="Название ЖК" value={cq} onChange={(e) => setCq(e.target.value)} />{cq && <button onClick={() => setCq('')}><IcClose size={18} /></button>}</div></div>
          <Cell title="Определить автоматически" end={!complexId ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined} onClick={() => { setComplexId(''); setModal(false) }} />
          {complexes.filter((x) => x.name.toLowerCase().includes(cq.toLowerCase())).map((x) => (
            <Cell key={x.id} inset icon={<img className="th" src={x.cover} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />} title={x.name} sub={x.address}
              end={complexId === x.id ? <IcCheck size={22} strokeWidth={2.5} style={{ color: 'var(--text)' }} /> : undefined} onClick={() => { setComplexId(x.id); setModal(false) }} />
          ))}
        </Modal>
      )}
    </>
  )
}
