import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ScanText, Sparkles, Upload } from 'lucide-react'
import { Highlight, PageHead, Score, ZoneBadge, platformMeta } from '../components/ui'
import type { Ad, Platform } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'

const SAMPLE = 'Старт продаж в новом ЖК «Тихий берег»! Квартиры у набережной, бронь от 300 000 ₸, первый взнос 15%, рассрочка от застройщика. Сдача 2027. Продаётся по договору долевого участия.'

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

  const suggestion = useMemo(() => {
    const t = `${text} ${ocr ?? ''}`.toLowerCase()
    return complexes.find((c) => t.includes(c.name.toLowerCase()))
  }, [text, ocr, complexes])
  const linked = complexes.find((c) => c.id === (complexId || suggestion?.id))
  const draft: Ad = { id: `m${Date.now().toString(36)}`, platform, account: account || '@unknown', url, date: new Date().toISOString(), text, ocr, image: img, complexId: linked?.id, matchConfidence: complexId ? 1 : suggestion ? 0.8 : 0, source: 'manual', status: 'new' }
  const s = scoreAd(draft, linked)

  const onFile = (f?: File) => {
    if (!f) return
    setImg(URL.createObjectURL(f))
    setOcrBusy(true)
    // Имитация OCR: в бэкенде — Tesseract (rus+kaz) через /api/ocr
    setTimeout(() => { setOcr('СТАРТ ПРОДАЖ · БРОНЬ · РАССРОЧКА 0% · СДАЧА 2027'); setOcrBusy(false) }, 1100)
  }

  return (
    <div className="page">
      <PageHead idx="03.1" label="Ручной ввод" title={<>Новая <span className="serif-i">публикация</span></>}>
        <p className="ink2" style={{ maxWidth: 620, marginTop: 18 }}>Обязательный источник по ТЗ: аналитик добавляет найденную рекламу вручную — ссылку, текст и скриншот. Система распознаёт текст, привязывает ЖК и сразу показывает оценку.</p>
      </PageHead>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48 }}>
        <div className="stack" style={{ gap: 20 }}>
          <div className="field"><span className="label">Площадка</span>
            <div className="row wrap" style={{ gap: 6 }}>{(Object.keys(platformMeta) as Platform[]).map((p) => <button key={p} className={`chip ${platform === p ? 'on' : ''}`} onClick={() => setPlatform(p)}>{platformMeta[p].name}</button>)}</div>
          </div>
          <div className="grid g-2" style={{ gap: 16 }}>
            <label className="field"><span className="label">Аккаунт / автор</span><input className="input" placeholder="@account" value={account} onChange={(e) => setAccount(e.target.value)} /></label>
            <label className="field"><span className="label">Ссылка</span><input className="input" placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} /></label>
          </div>
          <label className="field">
            <span className="row" style={{ justifyContent: 'space-between' }}><span className="label">Текст объявления</span><button className="btn sm ghost" onClick={() => setText(SAMPLE)}><Sparkles size={12} />Пример</button></span>
            <textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} placeholder="Вставьте текст поста, описание видео или объявления…" />
          </label>
          <div className="field"><span className="label">Скриншот / изображение</span>
            <div className="drop" onClick={() => file.current?.click()} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); onFile(e.dataTransfer.files[0]) }}>
              {img ? <img src={img} alt="" style={{ maxHeight: 260 }} /> : (
                <div className="stack" style={{ alignItems: 'center' }}><Upload size={22} /><div>Перетащите файл или нажмите</div><span className="label">PNG · JPG · до 10 МБ · OCR rus/kaz</span></div>
              )}
            </div>
            <input ref={file} type="file" accept="image/*" hidden onChange={(e) => onFile(e.target.files?.[0])} />
            {(ocrBusy || ocr) && <div className="row mono" style={{ fontSize: 12, marginTop: 6 }}><ScanText size={14} />{ocrBusy ? 'Распознаём текст…' : <>OCR: <Highlight text={ocr!} /></>}</div>}
          </div>
          <label className="field"><span className="label">Привязка к ЖК</span>
            <select className="select" value={complexId} onChange={(e) => setComplexId(e.target.value)}>
              <option value="">{suggestion ? `Авто: ${suggestion.name}` : 'Определить автоматически'}</option>
              {complexes.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.district}</option>)}
            </select>
          </label>
          <div className="row">
            <button className="btn primary" disabled={!text} style={{ opacity: text ? 1 : 0.4 }} onClick={() => { addAd(draft); notify('Публикация добавлена в ленту'); nav(`/ads/${draft.id}`) }}>Сохранить и оценить</button>
            <button className="btn ghost" onClick={() => nav('/ads')}>Отмена</button>
          </div>
        </div>

        <aside style={{ position: 'sticky', top: 80, alignSelf: 'start' }}>
          <div className="label" style={{ marginBottom: 12 }}>Предпросмотр анализа · обновляется на лету</div>
          <div className="card">
            {img && <div className="ph-frame" style={{ height: 220 }}><img className="img" src={img} alt="" /></div>}
            <div className="card-pad stack">
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <div className="row"><Score value={s.score} zone={s.zone} solid /><ZoneBadge zone={s.zone} /></div>
                <span className="label">{linked ? `→ ${linked.name}` : 'ЖК не определён'}</span>
              </div>
              <div style={{ fontSize: 16, lineHeight: 1.55, minHeight: 120 }}>{text ? <Highlight text={text} /> : <span className="muted">Текст появится здесь с подсветкой ключевых формулировок</span>}</div>
              <hr className="rule" />
              <div className="grid g-3" style={{ gap: 12 }}>
                <div><div className="label">Продажа</div><div className="num" style={{ fontSize: 34 }}>{s.signals.sale.length}</div></div>
                <div><div className="label">Сбор денег</div><div className="num" style={{ fontSize: 34, color: 'var(--red)' }}>{s.signals.funds.length}</div></div>
                <div><div className="label">Законность</div><div className="num" style={{ fontSize: 34, color: 'var(--green)' }}>{s.signals.safe.length}</div></div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
