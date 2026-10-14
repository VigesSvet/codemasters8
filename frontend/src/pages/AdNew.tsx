import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button, ButtonSet, FileUploaderDropContainer, Form, InlineLoading, Select, SelectItem, Stack, TextArea, TextInput, Tile, RadioButton, RadioButtonGroup,
} from '@carbon/react'
import { Highlight, PageHeader, Score, ZoneTag, platformMeta } from '../components/ui'
import type { Ad, Platform } from '../data/types'
import { scoreAd } from '../lib/risk'
import { useStore } from '../lib/store'

const SAMPLE = 'Старт продаж в новом ЖК «Тихий берег»! Квартиры у набережной, бронь от 300 000 ₸, первый взнос 15%, рассрочка от застройщика. Сдача 2027. Продаётся по договору долевого участия.'

export default function AdNew() {
  const { complexes, addAd, notify } = useStore()
  const nav = useNavigate()
  const [platform, setPlatform] = useState<Platform>('instagram')
  const [account, setAccount] = useState('')
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [img, setImg] = useState<string | undefined>()
  const [ocr, setOcr] = useState<string | undefined>()
  const [ocrBusy, setOcrBusy] = useState(false)
  const [complexId, setComplexId] = useState('')

  const suggestion = useMemo(() => complexes.find((c) => `${text} ${ocr ?? ''}`.toLowerCase().includes(c.name.toLowerCase())), [text, ocr, complexes])
  const linked = complexes.find((c) => c.id === (complexId || suggestion?.id))
  const draft: Ad = { id: `m${Date.now().toString(36)}`, platform, account: account || '@unknown', url, date: new Date().toISOString(), text, ocr, image: img, complexId: linked?.id, matchConfidence: complexId ? 1 : suggestion ? 0.8 : 0, source: 'manual', status: 'new' }
  const s = scoreAd(draft, linked)

  const onFiles = (files: File[]) => {
    const f = files[0]
    if (!f) return
    setImg(URL.createObjectURL(f))
    setOcrBusy(true)
    // В бэкенде: POST /api/ocr (Tesseract rus+kaz). Здесь — демо-результат.
    setTimeout(() => { setOcr('СТАРТ ПРОДАЖ · БРОНЬ · РАССРОЧКА 0% · СДАЧА 2027'); setOcrBusy(false) }, 1100)
  }

  return (
    <>
      <PageHeader crumbs={[['SAQ', '/'], ['Мониторинг рекламы', '/ads'], ['Новая публикация']]} title="Новая публикация"
        sub="Обязательный источник по ТЗ: аналитик добавляет найденную рекламу вручную. Система распознаёт текст, привязывает ЖК и сразу показывает оценку." />
      <div className="page" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 640px) 1fr', gap: 48, alignItems: 'start' }}>
        <Form onSubmit={(e) => e.preventDefault()}>
          <Stack gap={6}>
            <RadioButtonGroup legendText="Площадка" name="platform" valueSelected={platform} onChange={(v) => setPlatform(v as Platform)} orientation="horizontal">
              {(Object.keys(platformMeta) as Platform[]).map((p) => <RadioButton key={p} id={`p-${p}`} value={p} labelText={platformMeta[p].name} />)}
            </RadioButtonGroup>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <TextInput id="acc" labelText="Аккаунт / автор" placeholder="@account" value={account} onChange={(e) => setAccount(e.target.value)} />
              <TextInput id="url" labelText="Ссылка на публикацию" placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} />
            </div>
            <div>
              <TextArea id="txt" labelText="Текст объявления" helperText="Текст поста, описание видео или объявления" rows={6} value={text} onChange={(e) => setText(e.target.value)} />
              <Button kind="ghost" size="sm" onClick={() => setText(SAMPLE)} style={{ marginTop: 4 }}>Вставить пример</Button>
            </div>
            <div>
              <p className="cds--file--label">Скриншот</p>
              <p className="cds--label-description">PNG или JPG до 10 МБ. Текст на изображении распознаётся (rus/kaz).</p>
              <FileUploaderDropContainer labelText="Перетащите файл сюда или нажмите для выбора" accept={['image/*']} onAddFiles={(_e, { addedFiles }) => onFiles(addedFiles)} />
              {img && <img src={img} alt="" className="drop-preview" />}
              {ocrBusy && <InlineLoading description="Распознаём текст…" />}
              {ocr && !ocrBusy && <div className="mono" style={{ marginTop: 8 }}>OCR: <Highlight text={ocr} /></div>}
            </div>
            <Select id="cx" labelText="Привязка к ЖК" value={complexId} onChange={(e) => setComplexId(e.target.value)}>
              <SelectItem value="" text={suggestion ? `Автоматически: ЖК «${suggestion.name}»` : 'Определить автоматически'} />
              {complexes.map((c) => <SelectItem key={c.id} value={c.id} text={`ЖК «${c.name}» · ${c.district}`} />)}
            </Select>
            <ButtonSet>
              <Button kind="secondary" onClick={() => nav('/ads')}>Отмена</Button>
              <Button kind="primary" disabled={!text} onClick={() => { addAd(draft); notify('Публикация добавлена в ленту'); nav(`/ads/${draft.id}`) }}>Сохранить и оценить</Button>
            </ButtonSet>
          </Stack>
        </Form>

        <div className="sticky">
          <div className="label-01" style={{ marginBottom: 8 }}>Предпросмотр анализа · обновляется по мере ввода</div>
          <Tile>
            {img && <img src={img} alt="" style={{ width: '100%', height: 200, objectFit: 'cover', marginBottom: 16 }} />}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><Score value={s.score} zone={s.zone} /><ZoneTag zone={s.zone} size="sm" /></div>
              <span className="label-01">{linked ? `→ ЖК «${linked.name}»` : 'ЖК не определён'}</span>
            </div>
            <p style={{ fontSize: 16, lineHeight: 1.5, minHeight: 120, marginTop: 16 }}>{text ? <Highlight text={text} /> : <span className="muted">Текст появится здесь с подсветкой ключевых формулировок</span>}</p>
            <div className="facts" style={{ gridTemplateColumns: 'repeat(3, 1fr)', margin: '16px 0 0' }}>
              <div><div className="l">Продажа</div><div className="v">{s.signals.sale.length}</div></div>
              <div><div className="l">Сбор денег</div><div className="v" style={{ color: 'var(--zone-red)' }}>{s.signals.funds.length}</div></div>
              <div><div className="l">Законность</div><div className="v" style={{ color: 'var(--zone-green)' }}>{s.signals.safe.length}</div></div>
            </div>
          </Tile>
        </div>
      </div>
    </>
  )
}
