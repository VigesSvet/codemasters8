// «Решение аналитика» рядом с «Проверкой по рекламе» в карточке ЖК: «Объект сдан» (ввод в эксплуатацию подтверждён) и
// «Проверено — замечаний нет» закрывают «Требует проверки» с источником; сводка действующего решения и «Отменить решение»;
// решение, снятое новой рекламой, — янтарное уведомление со ссылкой на публикацию, кнопки снова доступны. Запись — на сервере
// (lib/store.tsx → app.decisions); в офлайн-копии кнопки недоступны («доступно с сервером»). Тексты и проверки — lib/decision.ts.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { Complex } from '../data/types'
import {
  CANCEL_TITLE, DECISION_SOURCES, DECISION_TITLE, cancelBody, cancelHint, commissionedBody, decisionButtons, decisionSummary, isActive,
  newCommissioned, newNoIssues, noIssuesBody, reopenedNotice, validateCancel, validateCommissioned, validateNoIssues, type DecisionDlg,
} from '../lib/decision'
import type { From } from '../lib/nav'
import { statusOf } from '../lib/screening'
import { useStore } from '../lib/store'
import { errorText } from '../lib/api'
import { todayLocal, type FormErrors } from '../lib/verification'
import { IcBuilding, IcCheck, IcClose, IcMegaphone } from '../ui/icons'
import { SrcLink } from './Provenance'
import { Modal, adKey } from './ui'
import { OFFLINE, Row, SourcePick, Who } from './Verification'

const HINT = 'Решение записывается на сервере с автором и временем — в офлайн-копии недоступно'

/** Блок «Решение аналитика»: кнопки, сводка решения, уведомление о снятии новой рекламой и окна */
export function DecisionPanel({ c, live, state, onSaved }: { c: Complex; live: boolean; state?: { from: From }; onSaved?: (msg: string) => void }) {
  const [dlg, setDlg] = useState<DecisionDlg | null>(null)
  // сервер без решений аналитика (старой версии) поля decision не присылает — блока нет
  if (c.decision === undefined) return null
  const d = c.decision
  const status = statusOf(c)
  const buttons = decisionButtons(status, d)
  const notice = reopenedNotice(d)
  const active = isActive(d)
  const off = live ? undefined : HINT
  const btn = (primary: boolean) => `btn ${primary ? 'y' : 'g'} s`
  return (
    <section className={`scr-dec ${active ? 'on' : ''} ${buttons === 'primary' ? 'ask' : ''}`} aria-label="Решение аналитика">
      <div className="scr-dec-h">
        <b>Решение аналитика</b>
        {!d && <span className="t-sm c2">{buttons === 'primary'
          ? 'Дом сдан или случай проверен — закройте «Требует проверки» решением с источником'
          : 'Можно подтвердить ввод в эксплуатацию или записать «проверено — замечаний нет»'}</span>}
      </div>
      {notice && d?.reopenedBy && (
        <div className="box warn scr-dec-reopen t-sm" role="status">
          <IcMegaphone size={18} />
          <div>
            <b>{notice}</b>
            <div className="t-xs" style={{ marginTop: 4 }}>
              <Link className="link" to={`/ads/${encodeURIComponent(d.reopenedBy.adId)}`} state={state}>Публикация {adKey(d.reopenedBy.adId)}</Link>
              {' · '}прежнее решение: {decisionSummary(d)}
            </div>
          </div>
        </div>
      )}
      {active && (
        <div className="scr-dec-card">
          <span className="zone-pic green" style={{ width: 28, height: 28 }}>{d.kind === 'commissioned' ? <IcBuilding size={15} /> : <IcCheck size={15} />}</span>
          <div className="grow t-sm">
            <b>{decisionSummary(d)}</b>
            {(d.sourceUrl || d.comment) && (
              <div className="t-xs c2" style={{ marginTop: 3 }}>
                {d.sourceUrl && <SrcLink url={d.sourceUrl}>источник</SrcLink>}{d.sourceUrl && d.comment && ' · '}{d.comment && <>Комментарий: {d.comment}</>}
              </div>
            )}
            <div className="t-xs c2" style={{ marginTop: 3 }}>{d.kind === 'commissioned'
              ? 'Новая реклама со стартом продаж или строящейся очередью снимет решение автоматически.'
              : 'Новая реклама с признаком приёма денег снимет решение автоматически.'}</div>
          </div>
        </div>
      )}
      <div className="vf-acts" style={{ marginTop: 0 }}>
        {buttons && (
          <>
            <button type="button" className={btn(buttons === 'primary')} disabled={!live} title={off ?? 'Ввод в эксплуатацию подтверждён: акт, дата, источник'}
              onClick={() => setDlg({ kind: 'commissioned', form: newCommissioned() })}>
              <IcBuilding size={14} />{DECISION_TITLE.commissioned}
            </button>
            <button type="button" className={btn(false)} disabled={!live} title={off ?? 'Случай проверен: что проверено и где'}
              onClick={() => setDlg({ kind: 'no_issues', form: newNoIssues() })}>
              <IcCheck size={14} />{DECISION_TITLE.no_issues}
            </button>
          </>
        )}
        {d && (
          <button type="button" className="link t-sm vf-edit" disabled={!live} title={off ?? 'Оценка снова станет автоматической'}
            onClick={() => setDlg({ kind: 'cancel', form: { reason: '' } })}>
            <IcClose size={14} />{CANCEL_TITLE}
          </button>
        )}
        {!live && <span className="t-xs c2">{OFFLINE}</span>}
      </div>
      <DecisionDialogs c={c} dlg={dlg} setDlg={setDlg} onSaved={onSaved} />
    </section>
  )
}

/** Окна решения: «Объект сдан», «Проверено — замечаний нет», «Отменить решение» — проверка по-русски, ошибки сервера — в окне */
function DecisionDialogs({ c, dlg, setDlg, onSaved }: { c: Complex; dlg: DecisionDlg | null; setDlg: (d: DecisionDlg | null) => void; onSaved?: (msg: string) => void }) {
  const store = useStore()
  const { user } = store
  if (!dlg) return null
  const close = () => setDlg(null)
  const today = todayLocal()
  const run = (errors: FormErrors, send: () => Promise<unknown>, msg: string) => {
    if (dlg.busy) return
    if (Object.keys(errors).length) { setDlg({ ...dlg, errors, err: undefined }); return }
    setDlg({ ...dlg, busy: true, err: undefined, errors: {} })
    send().then(() => { setDlg(null); onSaved?.(msg) }, (e: unknown) => setDlg({ ...dlg, busy: false, err: errorText(e) }))
  }
  const errs = dlg.errors ?? {}
  const err = dlg.err && <div className="alog-err t-sm" style={{ margin: '8px 24px 0' }}>Не сохранено: {dlg.err}</div>
  const footer = (label: string, submit: () => void, cls = 'y') => (
    <><button className="btn g" onClick={close}>Отмена</button><button className={`btn ${cls}`} disabled={dlg.busy} onClick={submit}>{dlg.busy ? 'Записываем…' : label}</button></>
  )

  if (dlg.kind === 'commissioned') {
    const f = dlg.form
    const set = (patch: Partial<typeof f>) => setDlg({ ...dlg, form: { ...f, ...patch }, err: undefined, errors: {} })
    const submit = () => run(validateCommissioned(f, today), () => store.markCommissioned(c.id, commissionedBody(f, '')), 'Записано: объект сдан — замечаний нет')
    return (
      <Modal title={`${DECISION_TITLE.commissioned}: ЖК «${c.name}»`} onClose={close} footer={footer('Записать: объект сдан', submit)}>
        <p className="t-sm c2" style={{ padding: '0 24px 8px', lineHeight: '20px' }}>Ввод в эксплуатацию подтверждён — продажа квартир в сданном доме законна.
          Оценка станет «Дом сдан — замечаний нет», стадия карточки — «введён». Новая реклама со стартом продаж или строящейся очередью снимет решение автоматически.</p>
        <div style={{ padding: '0 24px' }}>
          <Row label="Акт №" htmlFor="dc-act" error={errs.ref}>
            <input id="dc-act" className="input" maxLength={128} value={f.act} placeholder="Необязательно: например, 12/2026" onChange={(e) => set({ act: e.target.value })} />
          </Row>
          <Row label="Дата акта" htmlFor="dc-date" error={errs.date}>
            <input id="dc-date" className="input" type="date" max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} />
          </Row>
          <Row label="Источник" htmlFor="dc-src" error={errs.source}>
            <SourcePick id="dc-src" options={DECISION_SOURCES.commissioned} choice={f.choice} other={f.other} onChange={(p) => set(p)} />
          </Row>
          <Row label="Ссылка" htmlFor="dc-url" error={errs.url}>
            <input id="dc-url" className="input" type="url" inputMode="url" maxLength={512} value={f.url} placeholder="https://… — необязательно" onChange={(e) => set({ url: e.target.value })} />
          </Row>
          <Row label="Комментарий" htmlFor="dc-com" error={errs.comment} top>
            <textarea id="dc-com" className="input" style={{ minHeight: 64 }} maxLength={2000} value={f.comment} placeholder="Необязательно: например, сдана I очередь, дом 2" onChange={(e) => set({ comment: e.target.value })} />
          </Row>
        </div>
        {err}
        <Who user={user} what="подтвердил — имя, дата и время попадут в журнал" />
      </Modal>
    )
  }

  if (dlg.kind === 'no_issues') {
    const f = dlg.form
    const set = (patch: Partial<typeof f>) => setDlg({ ...dlg, form: { ...f, ...patch }, err: undefined, errors: {} })
    const submit = () => run(validateNoIssues(f), () => store.decideScreening(c.id, noIssuesBody(f, '')), 'Записано: проверено — замечаний нет')
    return (
      <Modal title={`${DECISION_TITLE.no_issues}: ЖК «${c.name}»`} onClose={close} footer={footer('Записать решение', submit)}>
        <p className="t-sm c2" style={{ padding: '0 24px 8px', lineHeight: '20px' }}>Случай проверен — замечаний нет. Оценка станет «Проверено — замечаний нет»;
          новая реклама с признаком приёма денег снимет решение автоматически.</p>
        <div style={{ padding: '0 24px' }}>
          <Row label="Что проверено" htmlFor="dn-why" error={errs.subject} top>
            <textarea id="dn-why" className="input" style={{ minHeight: 72 }} maxLength={2000} value={f.reason} placeholder="Например: сверено с ГАСК — продажи по разрешению МИО № …" onChange={(e) => set({ reason: e.target.value })} />
          </Row>
          <Row label="Источник" htmlFor="dn-src" error={errs.source}>
            <SourcePick id="dn-src" options={DECISION_SOURCES.no_issues} choice={f.choice} other={f.other} onChange={(p) => set(p)} />
          </Row>
          <Row label="Ссылка" htmlFor="dn-url" error={errs.url}>
            <input id="dn-url" className="input" type="url" inputMode="url" maxLength={512} value={f.url} placeholder="https://… — необязательно" onChange={(e) => set({ url: e.target.value })} />
          </Row>
          <Row label="Комментарий" htmlFor="dn-com" error={errs.comment} top>
            <textarea id="dn-com" className="input" style={{ minHeight: 64 }} maxLength={2000} value={f.comment} placeholder="Необязательно: входящий номер ответа" onChange={(e) => set({ comment: e.target.value })} />
          </Row>
        </div>
        {err}
        <Who user={user} what="проверил — имя, дата и время попадут в журнал" />
      </Modal>
    )
  }

  // отмена решения
  const f = dlg.form
  const submit = () => run(validateCancel(f), () => store.cancelDecision(c.id, cancelBody(f, '')), 'Решение отменено — оценка автоматическая')
  return (
    <Modal title={CANCEL_TITLE} onClose={close} footer={footer('Отменить решение', submit, 'd')}>
      {c.decision && <p className="t-sm" style={{ padding: '0 24px 8px', lineHeight: '20px' }}><b>{decisionSummary(c.decision)}</b></p>}
      <p className="t-sm c2" style={{ padding: '0 24px 8px', lineHeight: '20px' }}>{cancelHint(c)}</p>
      <div style={{ padding: '0 24px 4px' }}>
        <label className="t-sm c2" htmlFor="dx-why">Причина отмены</label>
        <textarea id="dx-why" className="input" style={{ marginTop: 6, minHeight: 72 }} maxLength={2000} value={f.reason} autoFocus
          placeholder="Например: акт относится к другой очереди" onChange={(e) => setDlg({ ...dlg, form: { reason: e.target.value }, err: undefined, errors: {} })} />
        {errs.comment && <div className="vf-err" role="alert">{errs.comment}</div>}
      </div>
      {err}
      <Who user={user} what="отменил — имя, дата и время попадут в журнал" />
    </Modal>
  )
}
