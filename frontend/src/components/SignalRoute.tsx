import { useEffect, useState } from 'react'
import type { Signal } from '../data/types'
import { ApiError, errorText } from '../lib/api'
import { useT } from '../lib/i18n'
import { OUTCOME, dueInfo, getRoute, isApprover, routeAction, type Route, type RouteAction, type RouteSummary } from '../lib/route'
import { useStore } from '../lib/store'
import { IcCheck } from '../ui/icons'
import { Cell, Modal, fmtDT } from './ui'

const COMMENT_MAX = 2000

/** Ячейка «Срок» реестра: этап и «осталось / просрочено», у рассмотренных — прочерк */
export function RouteDue({ r, now }: { r?: RouteSummary; now: number }) {
  const { t } = useT()
  if (!r) return <span className="c3">—</span>
  if (r.status !== 'new') return <span className="c3 t-sm">{t('route.closed')}</span>
  const d = dueInfo(r.dueAt, now)
  return (
    <div className="rt-due t-sm">
      <div>{r.stage === 'approval' ? t('route.approval') : t('route.analysis')}</div>
      {d && <span className={`tag ${d.tone === 'overdue' ? 'red' : d.tone === 'soon' ? 'yellow' : 'gray'}`} title={r.dueAt ? `Срок этапа — до ${fmtDT(r.dueAt)} (${r.slaDays} раб. дн.)` : undefined}>
        {d.tone === 'overdue' ? `${t('route.overdueTag')} · ` : ''}{d.text.replace(/^просрочено на /, '')}
      </span>}
    </div>
  )
}

type Dlg = { action: RouteAction; comment: string; busy?: boolean; err?: string }
const DLG: Record<RouteAction, { title: string; button: string; label: string; required: boolean; ph: string }> = {
  submit: { title: 'Передать на согласование', button: 'Передать', label: 'Комментарий для руководителя — необязательно', required: false,
    ph: 'Например: основание сверено с Homeportal и реестром ЕО, публикации привязаны' },
  approve: { title: 'Согласовать и подтвердить сигнал', button: 'Согласовать', label: 'Основание решения — необязательно (по умолчанию «Согласовано без замечаний»)', required: false,
    ph: 'Например: гарантии ЕО и разрешения МИО нет, в рекламе предлагают внести первый взнос' },
  return: { title: 'Вернуть аналитику', button: 'Вернуть аналитику', label: 'Что доработать — обязательно, попадёт в журнал', required: true,
    ph: 'Например: нет сверки разрешения МИО, привязка публикации не подтверждена' },
}

/** Маршрут рассмотрения в карточке сигнала: текущий этап и срок, история этапов, действия по роли */
export function RoutePanel({ sig, onChanged }: { sig: Signal; onChanged: () => void }) {
  const { user, notify } = useStore()
  const [route, setRoute] = useState<Route | null>(null)
  const [err, setErr] = useState<string>()
  const [dlg, setDlg] = useState<Dlg | null>(null)
  const ver = `${sig.id}:${sig.status}:${sig.journal.length}`
  useEffect(() => {
    let alive = true
    getRoute(sig.id).then((r) => { if (alive) { setRoute(r); setErr(undefined) } }, (e: unknown) => alive && setErr(errorText(e)))
    return () => { alive = false }
  }, [ver]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!route) return <div className="card"><div className="t-h3">Маршрут рассмотрения</div><p className="t-sm c2" style={{ marginTop: 10 }}>{err ? `Маршрут недоступен: ${err}` : 'Загружаем…'}</p></div>
  const due = dueInfo(route.dueAt, new Date(route.now))
  const lead = isApprover(user)
  const run = async () => {
    if (!dlg || dlg.busy) return
    const need = DLG[dlg.action].required
    if (need && !dlg.comment.trim()) return
    setDlg({ ...dlg, busy: true, err: undefined })
    try {
      const r = await routeAction(sig.id, dlg.action, user?.name ?? '', dlg.comment)
      setRoute(r)
      setDlg(null)
      notify(dlg.action === 'submit' ? `Сигнал ${sig.code} передан на согласование руководителю`
        : dlg.action === 'approve' ? `Сигнал ${sig.code} согласован и подтверждён` : `Сигнал ${sig.code} возвращён аналитику`)
      onChanged()
    } catch (e) {
      setDlg((d) => d && { ...d, busy: false, err: errorText(e) })
      if (e instanceof ApiError && e.status === 409) onChanged()
    }
  }

  return (
    <div className="card">
      <div className="row between"><span className="t-h3">Маршрут рассмотрения</span>
        {due && <span className={`tag ${due.tone === 'overdue' ? 'red' : due.tone === 'soon' ? 'yellow' : 'gray'}`}>{due.text}</span>}</div>
      <div className="kv" style={{ marginTop: 14, gridTemplateColumns: '110px 1fr' }}>
        <span>Этап</span><span className="bold">{route.stageLabel}</span>
        {route.dueAt && <><span>Срок этапа</span><span>до {fmtDT(route.dueAt)}<div className="t-sm c2">{route.slaDays} раб. дн. · пн–пт, по времени Алматы</div></span></>}
        <span>Сроки</span><span className="t-sm">разбор — {route.sla.analysis} раб. дн., согласование — {route.sla.approval}</span>
      </div>
      <ol className="rt-steps">
        {route.stages.map((s, i) => {
          const cur = i === route.stages.length - 1
          const cls = s.overdue ? 'late' : !cur || s.stage === 'confirmed' ? 'done' : 'cur'
          return (
            <li key={i} className={cur ? `${cls} cur` : cls}>
              <i />
              <div>
                <b>{s.label}</b>{s.overdue && <span className="tag red" style={{ marginLeft: 6, height: 20, fontSize: 11 }}>просрочен</span>}
                <div className="c2">{s.startedBy || '—'} · {fmtDT(s.startedAt)}{s.dueAt && <> · срок до {fmtDT(s.dueAt)}</>}</div>
                {s.comment && i > 0 && <div className="c2" style={{ overflowWrap: 'anywhere' }}>«{s.comment}»</div>}
                {s.outcome && s.endedAt && s.outcome !== 'confirmed' && s.outcome !== 'returned' && <div className="t-xs c3">{OUTCOME[s.outcome]} · {fmtDT(s.endedAt)}</div>}
              </div>
            </li>
          )
        })}
      </ol>
      {route.actions.includes('submit') && (
        <button className="btn y full" style={{ marginTop: 16 }} onClick={() => setDlg({ action: 'submit', comment: '' })}>Передать на согласование</button>
      )}
      {route.actions.includes('approve') && (lead ? (
        <div className="stack" style={{ gap: 8, marginTop: 16 }}>
          <button className="btn y full" onClick={() => setDlg({ action: 'approve', comment: '' })}><IcCheck />Согласовать</button>
          <button className="btn g full" onClick={() => setDlg({ action: 'return', comment: '' })}>Вернуть аналитику</button>
        </div>
      ) : <div className="ai-note" style={{ marginTop: 14 }}>Ожидает решения: {route.approver}. Войдите под этой ролью, чтобы согласовать или вернуть.</div>)}
      <p className="t-xs c2" style={{ marginTop: 12, lineHeight: '17px' }}>
        Согласует {route.approver}. «Подтвердить сигнал» и «Вернуть на доработку» в карточке закрывают маршрут сразу. Этапы, авторы и сроки — из журнала сигнала.
      </p>
      {dlg && (
        <Modal title={`${DLG[dlg.action].title}: ${sig.code}`} onClose={() => setDlg(null)}
          footer={<>
            <button className="btn g" onClick={() => setDlg(null)}>Отмена</button>
            <button className={`btn ${dlg.action === 'return' ? 'd' : 'y'}`} disabled={dlg.busy || (DLG[dlg.action].required && !dlg.comment.trim())} onClick={run}>
              {dlg.busy ? 'Сохраняем…' : DLG[dlg.action].button}
            </button>
          </>}>
          <Cell icon={<span className="ava">{user?.short}</span>} title={user?.name} sub={`${user?.role ?? ''} · автор, дата и время попадут в журнал сигнала`} />
          <div style={{ padding: '12px 24px 4px' }}>
            <label className="t-sm c2" htmlFor="rtc">{DLG[dlg.action].label}</label>
            <textarea id="rtc" className="input" style={{ marginTop: 6 }} autoFocus maxLength={COMMENT_MAX} value={dlg.comment}
              placeholder={DLG[dlg.action].ph} onChange={(e) => setDlg({ ...dlg, comment: e.target.value, err: undefined })} />
            {dlg.err && <div className="alog-err t-sm" style={{ marginTop: 8 }}>Не сохранено: {dlg.err}</div>}
            {dlg.action === 'submit' && <p className="t-xs c2" style={{ marginTop: 8 }}>Срок согласования — {route.sla.approval} раб. дн. с момента передачи.</p>}
          </div>
        </Modal>
      )}
    </div>
  )
}
