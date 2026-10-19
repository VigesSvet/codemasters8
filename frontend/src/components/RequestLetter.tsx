import { useEffect, useState } from 'react'
import { errorText } from '../lib/api'
import { REQUEST_TOPICS, type RequestTopic } from '../lib/audit'
import { ADDRESSEES, letterFile, previewLetter, saveBlob, type Addressee, type Letter, type LetterBlock } from '../lib/letter'
import { useStore } from '../lib/store'
import { IcCheck, IcDoc, IcDownload, IcShield } from '../ui/icons'
import { Cell, Modal } from './ui'

const COMMENT_MAX = 2000
const NOTE = 'Черновик для внутреннего маршрута — автоматической отправки нет'

/** Кнопка «Запрос в ЖАО / ЕО» и окно проекта запроса (T-78): адресат, темы, предпросмотр, PDF и DOCX */
export function RequestLetterButton({ complexId, complexName, topics, className = 'btn g full', label = 'Запрос в ЖАО / ЕО (письмо)', onSaved }: {
  complexId: string; complexName: string; topics?: RequestTopic[]; className?: string; label?: string; onSaved?: () => void
}) {
  const { mode } = useStore()
  const [open, setOpen] = useState(false)
  const live = mode === 'api'
  return (
    <>
      <button type="button" className={className} disabled={!live} onClick={() => setOpen(true)}
        title={live ? 'Проект официального запроса сведений: PDF или DOCX, без отправки' : 'Доступно с сервером'}><IcDoc />{label}</button>
      {open && <RequestLetter complexId={complexId} complexName={complexName} topics={topics} onClose={() => setOpen(false)} onSaved={onSaved} />}
    </>
  )
}

function RequestLetter({ complexId, complexName, topics: initial, onClose, onSaved }: {
  complexId: string; complexName: string; topics?: RequestTopic[]; onClose: () => void; onSaved?: () => void
}) {
  const { user, notify } = useStore()
  const [addressee, setAddressee] = useState<Addressee>('mio')
  const [topics, setTopics] = useState<RequestTopic[]>(initial?.length ? initial : ['basis'])
  const [comment, setComment] = useState('')
  const [preview, setPreview] = useState<{ key: string; letter?: Letter; err?: string } | null>(null)
  const [busy, setBusy] = useState<'pdf' | 'docx' | null>(null)
  const [err, setErr] = useState<string>()
  const ordered = REQUEST_TOPICS.map(([t]) => t).filter((t) => topics.includes(t))
  const valid = ordered.length > 0 && (!ordered.includes('other') || !!comment.trim()) && !!user
  const body = { addressee, author: user?.name ?? '', topics: ordered, comment }
  const key = JSON.stringify(body)

  // предпросмотр перечитывается после паузы в наборе: текст письма собирает сервер, в журнал он не пишется
  useEffect(() => {
    if (!valid) return
    let alive = true
    const t = setTimeout(() => {
      previewLetter(complexId, body).then(
        (letter) => alive && setPreview({ key, letter }),
        (e: unknown) => alive && setPreview({ key, err: errorText(e) }),
      )
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [key, valid]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (t: RequestTopic) => { setErr(undefined); setTopics((q) => (q.includes(t) ? q.filter((x) => x !== t) : [...q, t])) }
  const download = async (format: 'pdf' | 'docx') => {
    if (!valid || busy) return
    setBusy(format)
    setErr(undefined)
    try {
      const { blob, name } = await letterFile(complexId, body, format)
      saveBlob(blob, name)
      notify(`Проект запроса ${name} сформирован и записан в журнал объекта — во внешние органы не отправлялся`)
      onSaved?.()
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(null)
    }
  }
  const shown = preview?.key === key ? preview : null

  return (
    <Modal wide title="Проект запроса сведений" onClose={onClose}
      footer={<>
        <span className="t-xs c2 row" style={{ gap: 6, marginRight: 'auto', maxWidth: 380 }}><IcShield size={14} />{NOTE}</span>
        <button className="btn g" onClick={onClose}>Отмена</button>
        <button className="btn g" disabled={!valid || !!busy} onClick={() => download('docx')}><IcDownload />{busy === 'docx' ? 'Формируем…' : 'Скачать DOCX'}</button>
        <button className="btn y" disabled={!valid || !!busy} onClick={() => download('pdf')}><IcDownload />{busy === 'pdf' ? 'Формируем…' : 'Скачать PDF'}</button>
      </>}>
      <div className="rl-grid">
        <div>
          <Cell icon={<span className="ava">{user?.short}</span>} title={user?.name} sub={`${user?.role ?? ''} · в подписи — должность, без имени`} />
          <div className="t-sm c2" style={{ padding: '12px 24px 4px' }}>Адресат</div>
          <div role="radiogroup" aria-label="Адресат запроса">
            {ADDRESSEES.map(([a, name, hint]) => (
              <button key={a} type="button" role="radio" aria-checked={addressee === a} className="cell chk-row" onClick={() => setAddressee(a)}>
                <span className={`chk round ${addressee === a ? 'on' : ''}`}>{addressee === a && <IcCheck size={14} strokeWidth={3} />}</span>
                <div className="main"><div className="ttl">{name}</div><div className="sub">{hint}</div></div>
              </button>
            ))}
          </div>
          <div className="t-sm c2" style={{ padding: '12px 24px 4px' }}>Что запросить</div>
          <div role="group" aria-label="Темы запроса">
            {REQUEST_TOPICS.map(([t, label]) => {
              const on = topics.includes(t)
              return (
                <button key={t} type="button" role="checkbox" aria-checked={on} className="cell chk-row" onClick={() => toggle(t)}>
                  <span className={`chk ${on ? 'on' : ''}`}>{on && <IcCheck size={14} strokeWidth={3} />}</span>
                  <div className="main"><div className="ttl">{label[0].toUpperCase() + label.slice(1)}</div></div>
                </button>
              )
            })}
          </div>
          <div style={{ padding: '12px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="rlc">Комментарий{ordered.includes('other') ? ' — обязательно для темы «другое»: какие сведения запросить' : ' — необязательно, войдёт в письмо'}</label>
            <textarea id="rlc" className="input" style={{ marginTop: 6, minHeight: 80 }} maxLength={COMMENT_MAX} value={comment}
              placeholder="Например: уточнить, распространяется ли разрешение на очередь 2"
              onChange={(e) => { setComment(e.target.value); setErr(undefined) }} />
            {err && <div className="alog-err t-sm" style={{ marginTop: 8 }}>Не сформировано: {err}</div>}
          </div>
        </div>
        <div className="rl-prev" aria-label="Предпросмотр письма">
          {!valid ? <p className="c2 t-sm">Выберите хотя бы одну тему{ordered.includes('other') ? ' и опишите «другое» в комментарии' : ''}.</p>
            : !shown ? <p className="c2 t-sm">Собираем текст запроса по ЖК «{complexName}»…</p>
            : shown.err ? <div className="alog-err t-sm">Предпросмотр недоступен: {shown.err}</div>
            : shown.letter && <LetterView letter={shown.letter} />}
        </div>
      </div>
    </Modal>
  )
}

/** Текст письма как на листе: адресат справа, тема, пункты, сведения, нормы, приложения, подпись */
export function LetterView({ letter }: { letter: Letter }) {
  return <div className="rl-sheet">{letter.blocks.map((b, i) => <Block key={i} b={b} />)}</div>
}

function Block({ b }: { b: LetterBlock }) {
  switch (b.type) {
    case 'note': return <div className="ai-note warn t-xs" style={{ marginBottom: 12 }}>{b.text}</div>
    case 'addr': return <div className="rl-addr">{b.lines.map((l, i) => <div key={i}>{l}</div>)}</div>
    case 'meta': return <div className="rl-meta">{b.text}</div>
    case 'subject': return <div className="rl-subj">Тема: {b.text}</div>
    case 'h': return <div className="rl-h">{b.text}</div>
    case 'li': return <div className="rl-li"><span>{b.n}.</span><span>{b.text}</span></div>
    case 'bullet': return <div className="rl-li sm"><span>—</span><span>{b.text}</span></div>
    case 'sign': return <div className="rl-sign">{b.lines.map((l, i) => <div key={i}>{l}</div>)}</div>
    case 'small': return <div className="t-xs c2" style={{ marginTop: 8 }}>{b.text}</div>
    default: return <p className="rl-p">{b.text}</p>
  }
}
