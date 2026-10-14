import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Breadcrumb, BreadcrumbItem, Button, ComboBox, Dropdown, Tag, TextArea } from '@carbon/react'
import { Checkmark, Close, DocumentPdf, Help, Launch, UserAvatar } from '@carbon/icons-react'
import { Gauge, Highlight, PlatformIcon, Score, StatusIcon, ZoneTag, fmtDT, platformMeta, statusMeta } from '../components/ui'
import { audit } from '../data/demo'
import type { AdStatus } from '../data/types'
import { scoreAd, scoreComplex } from '../lib/risk'
import { useStore } from '../lib/store'

const ORDER: AdStatus[] = ['new', 'needs_check', 'in_review', 'confirmed', 'dismissed']

/** Разбор публикации — раскладка карточки задачи Linear: основной текст + активность слева, свойства справа */
export default function AdDetail() {
  const { id } = useParams()
  const { ads, complexes, setAdStatus, notify } = useStore()
  const a = ads.find((x) => x.id === id)
  const [comment, setComment] = useState('')
  const [notes, setNotes] = useState<{ at: string; who: string; what: string }[]>([])
  const [linkId, setLinkId] = useState<string | undefined>(a?.complexId)
  if (!a) return <div className="page">Публикация не найдена</div>
  const c = complexes.find((x) => x.id === linkId)
  const s = scoreAd(a, c)
  const cr = c ? scoreComplex(c, ads) : null
  const key = `AD-${a.id.replace(/\D/g, '').padStart(3, '0') || a.id}`
  const events = [...(audit[a.id] ?? [
    { at: fmtDT(a.date), who: 'Система', what: a.source === 'manual' ? 'добавил публикацию вручную' : 'собрал публикацию автоматически' },
    { at: fmtDT(a.date), who: 'Система', what: c ? `связал с ЖК «${c.name}» (уверенность ${(a.matchConfidence * 100).toFixed(0)}%)` : 'не смог определить ЖК — нужна ручная привязка' },
    { at: fmtDT(a.date), who: 'Система', what: `оценил публикацию в ${s.score} баллов` },
  ]), ...notes]

  const decide = (st: AdStatus, msg: string) => {
    setAdStatus(a.id, st, 'Нестерова А.')
    setNotes((n) => [...n, { at: fmtDT(new Date().toISOString()), who: 'Нестерова А.', what: `сменила статус на «${statusMeta[st].name}»${comment ? `: ${comment}` : ''}` }])
    setComment('')
    notify(msg)
  }

  return (
    <div className="issue-view">
      <div className="main">
        <Breadcrumb noTrailingSlash>
          <BreadcrumbItem><Link to="/ads">Мониторинг рекламы</Link></BreadcrumbItem>
          <BreadcrumbItem isCurrentPage>{key}</BreadcrumbItem>
        </Breadcrumb>
        <h1 className="cds--type-productive-heading-05" style={{ margin: '16px 0 8px' }}>{c ? `Реклама ЖК «${c.name}»` : 'Реклама без привязки к ЖК'}</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 24 }}>
          <PlatformIcon p={a.platform} /><span className="muted">{platformMeta[a.platform].name} · {a.account} · {fmtDT(a.date)}</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: a.image ? '280px 1fr' : '1fr', gap: 24 }}>
          {a.image && <img src={a.image} alt="" style={{ width: 280, height: 280, objectFit: 'cover' }} />}
          <div>
            <p style={{ fontSize: 18, lineHeight: 1.5 }}><Highlight text={a.text} /></p>
            {a.ocr && (
              <div style={{ marginTop: 16, padding: 12, background: 'var(--cds-layer-02)' }}>
                <div className="label-01" style={{ marginBottom: 6 }}>Распознано на изображении (OCR)</div>
                <div className="mono"><Highlight text={a.ocr} /></div>
              </div>
            )}
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 16 }}>
              {a.price && <Tag type="cool-gray">Цена: {a.price}</Tag>}
              {a.reach && <Tag type="cool-gray">Охват ~{a.reach.toLocaleString('ru')}</Tag>}
              <Tag type="cool-gray">{a.source === 'manual' ? 'Добавлено вручную' : a.source === 'parser' ? 'Парсер' : 'Демо'}</Tag>
            </div>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginTop: 32 }}>
          {([['Признаки продажи', s.signals.sale, ''], ['Признаки сбора денег', s.signals.funds, 'fund'], ['Признаки законности', s.signals.safe, 'safe']] as const).map(([t, list, cls]) => (
            <div key={t}>
              <div className="label-01" style={{ marginBottom: 8 }}>{t}</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>{list.length ? list.map((k) => <mark key={k} className={cls}>{k}</mark>) : <span className="muted">—</span>}</div>
            </div>
          ))}
        </div>

        <div className="activity">
          <h4 className="cds--type-heading-compact-02" style={{ marginBottom: 12 }}>Активность</h4>
          {events.map((e, i) => (
            <div className="act" key={i}>
              {e.who === 'Система' ? <span className="avatar" style={{ background: 'var(--cds-layer-accent-01)', color: 'var(--cds-text-secondary)' }}>S</span> : <span className="avatar">ВН</span>}
              <div><span className="who">{e.who}</span> {e.what.charAt(0).toLowerCase() + e.what.slice(1)} <span className="muted"> · {e.at}</span></div>
            </div>
          ))}
          <TextArea labelText="Комментарий" hideLabel placeholder="Оставьте комментарий: на чём основано решение, с чем сверено…" rows={3} value={comment} onChange={(e) => setComment(e.target.value)} style={{ marginTop: 12 }} />
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
            <Button size="sm" kind="secondary" disabled={!comment} onClick={() => { setNotes((n) => [...n, { at: fmtDT(new Date().toISOString()), who: 'Нестерова А.', what: `прокомментировала: ${comment}` }]); setComment('') }}>Отправить</Button>
          </div>
        </div>
      </div>

      <aside className="props">
        <div className="prop"><span>Статус</span>
          <Dropdown id="st" size="sm" titleText="" hideLabel label="" items={ORDER} selectedItem={a.status} itemToString={(x) => (x ? statusMeta[x as AdStatus].name : '')}
            renderSelectedItem={(x) => <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}><StatusIcon s={x as AdStatus} />{statusMeta[x as AdStatus].name}</span>}
            onChange={({ selectedItem }) => selectedItem && decide(selectedItem as AdStatus, `Статус: ${statusMeta[selectedItem as AdStatus].name}`)} />
        </div>
        <div className="prop"><span>Ответственный</span><span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>{a.assignee ? <><span className="avatar">ВН</span>{a.assignee}</> : <><UserAvatar size={20} /><span className="muted">Не назначен</span></>}</span></div>
        <div className="prop"><span>Площадка</span><span style={{ display: 'flex', gap: 8, alignItems: 'center' }}><PlatformIcon p={a.platform} />{platformMeta[a.platform].name}</span></div>
        <div className="prop"><span>Источник</span><a href={a.url} target="_blank" rel="noreferrer" style={{ display: 'flex', gap: 4, alignItems: 'center' }}>Открыть <Launch size={14} /></a></div>

        <h5>Связанный ЖК</h5>
        <ComboBox id="cx" size="sm" titleText="" placeholder="Выберите ЖК" items={complexes} selectedItem={c ?? null}
          itemToString={(x) => (x ? `ЖК «${x.name}» · ${x.district}` : '')} onChange={({ selectedItem }) => { setLinkId(selectedItem?.id); notify('Привязка изменена, скоринг пересчитан', 'info') }} />
        {c && cr && (
          <Link to={`/complexes/${c.id}`} style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 12, textDecoration: 'none', color: 'inherit' }}>
            <img src={c.cover} alt="" style={{ width: 56, height: 42, objectFit: 'cover' }} />
            <div style={{ flex: 1 }}><div className="cell-title">ЖК «{c.name}»</div><div className="cell-sub">уверенность {(a.matchConfidence * 100).toFixed(0)}%</div></div>
            <Score value={cr.score} zone={cr.zone} />
          </Link>
        )}

        <h5>Оценка публикации</h5>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}><span style={{ fontSize: 40, fontWeight: 300, lineHeight: 1 }}>{s.score}</span><ZoneTag zone={s.zone} size="sm" /></div>
        <Gauge value={s.score} />
        {cr && cr.factors.slice(0, 3).map((f) => (
          <div key={f.code} className={`factor ${f.state}`}><span className="code">{f.code}</span><div>{f.title}<div className="e">{f.evidence}</div></div><span className="pts">+{f.points}</span></div>
        ))}

        <h5>Решение аналитика</h5>
        <div style={{ display: 'grid', gap: 1 }}>
          <Button kind="danger" size="md" renderIcon={Checkmark} style={{ maxWidth: 'none' }} onClick={() => decide('confirmed', 'Нарушение подтверждено')}>Подтвердить нарушение</Button>
          <Button kind="secondary" size="md" renderIcon={Close} style={{ maxWidth: 'none' }} onClick={() => decide('dismissed', 'Публикация отклонена')}>Отклонить</Button>
          <Button kind="tertiary" size="md" renderIcon={Help} style={{ maxWidth: 'none' }} onClick={() => decide('needs_check', 'Отправлено на доп. проверку')}>Нужна доп. проверка</Button>
          <Button kind="ghost" size="md" renderIcon={DocumentPdf} style={{ maxWidth: 'none' }} onClick={() => notify('Акт фиксации сформирован (PDF, демо)', 'info')}>Акт фиксации</Button>
        </div>
        <p className="helper" style={{ marginTop: 12 }}>ИИ даёт рекомендацию. Решение принимает сотрудник; автоматической отправки в госорганы нет.</p>
      </aside>
    </div>
  )
}
