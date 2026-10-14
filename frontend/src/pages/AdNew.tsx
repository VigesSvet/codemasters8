import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Cell, Highlight, Modal, PageTitle, PlatformIcon, Score, ZoneTag, platformMeta } from '../components/ui'
import type { Ad, Platform } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcBuilding, IcCheck, IcClose, IcPlus, IcSearch, IcUpload } from '../ui/icons'

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

      <div style={{ display: 'grid', gridTemplateColumns: '520px minmax(0, 1fr)', gap: 16, alignItems: 'start' }}>
        <div className="stack">
          <div className="card">
            <div className="tiles" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
              {(Object.keys(platformMeta) as Platform[]).map((p) => (
                <button key={p} className={`tile ${platform === p ? 'on' : ''}`} onClick={() => setPlatform(p)} style={{ minHeight: 78, alignItems: 'flex-start' }}>
                  <span className="pic" style={{ height: 30, marginBottom: 4 }}><PlatformIcon p={p} size={30} /></span>
                  <span className="nm" style={{ fontSize: 14 }}>{platformMeta[p].name}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="card">
            <div className="field">
              <span className="mk"><span className="mk ring" /></span>
              <div className="body"><label>Аккаунт или автор</label><input placeholder="@account" value={account} onChange={(e) => setAccount(e.target.value)} /></div>
            </div>
            <div className="field">
              <span className="mk"><span className="mk ring dark" /></span>
              <div className="body"><label>Ссылка на публикацию</label><input placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} /></div>
            </div>
          </div>
          <div className="card">
            <div className="field top">
              <div className="body"><label>Текст объявления</label><textarea placeholder="Текст поста, описание видео или объявления…" value={text} onChange={(e) => setText(e.target.value)} /></div>
            </div>
            {!text && <Cell icon={<span className="c2"><IcPlus /></span>} title="Вставить пример" onClick={() => setText(SAMPLE)} />}
          </div>
          <div className="card" style={{ padding: 8 }}>
            <div className={`drop ${over ? 'over' : ''}`} onClick={() => file.current?.click()} onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={(e) => { e.preventDefault(); setOver(false); onFile(e.dataTransfer.files[0]) }}>
              {img ? <img src={img} alt="" style={{ maxHeight: 220, borderRadius: 12 }} /> : (
                <div><IcUpload size={28} /><div className="bold" style={{ marginTop: 8 }}>Скриншот публикации</div><div className="t-sm c2" style={{ marginTop: 4 }}>Перетащите или нажмите · PNG, JPG до 10 МБ</div></div>
              )}
            </div>
            <input ref={file} type="file" accept="image/*" hidden onChange={(e) => onFile(e.target.files?.[0])} />
            {(ocrBusy || ocr) && <div className="t-sm" style={{ padding: '12px 16px 6px' }}>{ocrBusy ? <span className="c2">Распознаём текст…</span> : <><span className="c2">OCR: </span><Highlight text={ocr!} /></>}</div>}
          </div>
          <div className="card">
            <Cell icon={<span className="ico"><IcBuilding size={20} /></span>} title="Объект" sub={complexId ? 'выбран вручную' : suggestion ? 'определён по тексту' : 'определится по тексту'} end={linked ? <span style={{ color: 'var(--text)' }}>{linked.name}</span> : 'не выбран'} chevron onClick={() => setModal(true)} />
          </div>
          <div className="card" style={{ padding: 16 }}>
            <button className="btn y full" disabled={!text} onClick={() => { addAd(draft); notify('Публикация добавлена в ленту'); nav(`/ads/${draft.id}`) }}>{text ? 'Сохранить и оценить' : 'Введите текст объявления'}</button>
          </div>
        </div>

        <div className="sticky stack">
          <div className="card">
            <div className="card-head"><h3>Предпросмотр</h3><span className="t-sm c2">обновляется по мере ввода</span></div>
            {img && <div className="ph" style={{ height: 240, margin: '4px 8px', borderRadius: 18 }}><img src={img} alt="" /></div>}
            <div className="card-pad">
              <div className="row between"><div className="row" style={{ gap: 8 }}><PlatformIcon p={platform} /><span className="bold">{account || '@account'}</span></div><div className="row" style={{ gap: 8 }}><Score value={s.score} zone={s.zone} /><ZoneTag zone={s.zone} /></div></div>
              <p style={{ fontSize: 18, lineHeight: '27px', minHeight: 110, marginTop: 16 }}>{text ? <Highlight text={text} /> : <span className="c3">Текст появится здесь с подсветкой ключевых формулировок</span>}</p>
            </div>
            <div className="tiles">
              {([['Продажа', s.signals.sale.length, 'var(--text)'], ['Сбор денег', s.signals.funds.length, 'var(--red)'], ['Законность', s.signals.safe.length, 'var(--green)']] as const).map(([k, v, col]) => (
                <div key={k} className="tile" style={{ background: 'var(--bg)', minHeight: 0 }}><span className="nm c2" style={{ fontSize: 13 }}>{k}</span><span className="vl num" style={{ fontSize: 28, color: col }}>{v}</span></div>
              ))}
            </div>
          </div>
          <div className="card">
            <Cell icon={<span className="ico"><IcBuilding size={20} /></span>} title={linked ? `ЖК «${linked.name}»` : 'ЖК не определён'} sub={linked ? `${linked.address} · ${linked.stage}` : 'Укажите название ЖК в тексте или выберите вручную'} />
          </div>
        </div>
      </div>

      {modal && (
        <Modal title="Выберите объект" onClose={() => setModal(false)}>
          <div style={{ padding: '8px 16px' }}><div className="search"><IcSearch size={20} /><input autoFocus placeholder="Название ЖК" value={cq} onChange={(e) => setCq(e.target.value)} />{cq && <button onClick={() => setCq('')}><IcClose size={18} /></button>}</div></div>
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
