// Сверка сведений аналитиком в карточке ЖК: действия на плитках оснований, подсказка «Сверено N из 4», окна сверки
// документа и перечня ГАСК, подтверждения в один клик, готовности, обращений, «обращений нет — сверено», снимка площадки.
// Запись — на сервере (lib/store.tsx → app.verification); в офлайн-копии кнопки недоступны («доступно с сервером»).
import { useRef, type ReactNode } from 'react'
import type { BasisDoc, Complex, Tri } from '../data/types'
import { errorText } from '../lib/api'
import { useStore, type User } from '../lib/store'
import {
  ACTIVITIES, OTHER, SOURCES, TARGETS, appealBody, appealsCheckBody, checkBody, checkOf, checkedLine, confirmBody, confirmLines,
  initialForm, photoFields, progress, progressText, readinessBody, targetTitle, todayLocal, validateAppeal,
  validateAppealsCheck, validateCheck, validatePhoto, validateReadiness,
  type AppealForm, type AppealsCheckForm, type CheckForm, type Dlg, type FormErrors, type PhotoForm, type ReadinessForm,
  type SourceKind, type VerifyTarget,
} from '../lib/verification'
import { IcCamera, IcCheck, IcDoc, IcPencil, IcPlus, IcUsers } from '../ui/icons'
import { SrcLink } from './Provenance'
import { Cell, Modal } from './ui'

export const OFFLINE = 'доступно с сервером'
const OFFLINE_HINT = 'Сверка записывается на сервере с автором и временем — в офлайн-копии недоступна'

// ---------- подсказка «Сверено N из 4» ----------

/** Сколько оснований сверено и кнопка к первому несверенному; всё сверено — ничего */
export function VerifyProgress({ c, assessed, onGo, style }: { c: Complex; assessed: boolean; onGo: (t: VerifyTarget) => void; style?: React.CSSProperties }) {
  const p = progress(c)
  const text = progressText(p, assessed)
  if (!text || !p.first) return null
  return (
    <div className="box warn vf-progress t-sm" style={style} role="status">
      <IcDoc size={18} />
      <div className="grow"><b>{text}</b>
        <div className="vf-dots" aria-hidden>{Array.from({ length: p.total }, (_, i) => <i key={i} className={i < p.checked ? 'on' : ''} />)}</div>
      </div>
      <button type="button" className="btn y s" onClick={() => onGo(p.first!)}>Сверить: {TARGETS.find((t) => t.key === p.first)?.short}</button>
    </div>
  )
}

// ---------- действие на плитке ----------

/** Кто и когда сверил: «Сверено: Омарова Ж., 19.10.2026 · Homeportal · источник» */
export function CheckedBy({ rec }: { rec?: { checkedBy?: string | null; checkedAt?: string | null; source?: string | null; url?: string | null; comment?: string | null } | null }) {
  const line = checkedLine(rec)
  if (!line) return null
  return (
    <span className="prov-f vf-ok" title={rec?.comment ? `Комментарий: ${rec.comment}` : 'Сверено аналитиком: автор и время — в журнале объекта'}>
      <IcCheck size={14} />
      <span>{line}{rec?.url && <> · <SrcLink url={rec.url} /></>}</span>
    </span>
  )
}

/** Кнопки внутри плитки основания: подтвердить подсказку реестра, сверить, изменить сверку */
export function TileActions({ c, target, checked, live, open }: {
  c: Complex; target: VerifyTarget; checked: boolean; live: boolean; open: (d: Dlg) => void
}) {
  const sg = target === 'list' || checked ? null : c.basisSuggest?.[target] ?? null
  const edit = () => open({ kind: 'check', target, form: initialForm(c, target) })
  if (checked) return (
    <div className="vf-acts">
      <button type="button" className="link t-sm vf-edit" disabled={!live} title={live ? 'Изменить или снять сверку' : OFFLINE_HINT} onClick={edit}>
        <IcPencil size={14} />{live ? 'Изменить сверку' : `Изменить — ${OFFLINE}`}
      </button>
    </div>
  )
  return (
    <div className="vf-acts">
      {sg ? (
        <>
          <button type="button" className="btn y s vf-main" disabled={!live} title={live ? 'Записать «есть» с номером, источником и ссылкой из открытого реестра' : OFFLINE_HINT}
            onClick={() => open({ kind: 'confirm', doc: target as BasisDoc, sg })}>
            <IcCheck size={14} />Подтвердить — совпадает с источником
          </button>
          <button type="button" className="btn g s" disabled={!live} title={live ? undefined : OFFLINE_HINT} onClick={edit}>Не совпадает / изменить</button>
        </>
      ) : (
        <button type="button" className="btn y s vf-main" disabled={!live} title={live ? 'Отметить «есть», «нет» или «нет сведений» с источником и датой' : OFFLINE_HINT} onClick={edit}>
          <IcCheck size={14} />Сверить
        </button>
      )}
      {!live && <span className="t-xs c2">{OFFLINE}</span>}
    </div>
  )
}

// ---------- окна ----------

const Err = ({ text }: { text?: string }) => (text ? <div className="vf-err" role="alert">{text}</div> : null)

export function Row({ label, htmlFor, error, top, children }: { label: string; htmlFor?: string; error?: string; top?: boolean; children: ReactNode }) {
  return (
    <div className={`frow ${top ? 'top' : ''}`}>
      <label htmlFor={htmlFor}>{label}</label>
      <div>{children}<Err text={error} /></div>
    </div>
  )
}

/** Источник: вариант из списка (по виду сведений или свой список options) или «Другое» со своим текстом */
export function SourcePick({ id, kind, options, choice, other, onChange }: {
  id: string; kind?: SourceKind; options?: readonly string[]; choice: string; other: string; onChange: (p: { choice?: string; other?: string }) => void
}) {
  return (
    <div className="vf-src">
      <select id={id} className="input" value={choice} onChange={(e) => onChange({ choice: e.target.value })}>
        <option value="" disabled>Выберите источник</option>
        {(options ?? (kind ? SOURCES[kind] : [])).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      {choice === OTHER && <input className="input" aria-label="Свой источник" autoFocus maxLength={255} value={other} placeholder="Например: письмо ДЭР № 15-02/311" onChange={(e) => onChange({ other: e.target.value })} />}
    </div>
  )
}

/** Выбор из вариантов (радиокнопки-чипы) */
function Choice<T extends string>({ value, items, onChange, label }: { value: T | ''; items: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="chips" role="radiogroup" aria-label={label}>
      {items.map(([k, l]) => (
        <button key={k} type="button" role="radio" aria-checked={value === k} className={`chip vf-chip ${k} ${value === k ? 'on' : ''}`} onClick={() => onChange(k)}>
          {value === k && <IcCheck size={14} />}{l}
        </button>
      ))}
    </div>
  )
}

const TRI_ITEMS: [Tri, string][] = [['yes', 'Есть'], ['no', 'Нет'], ['unknown', 'Нет сведений']]

export function Who({ user, what }: { user: User | null; what: string }) {
  return user ? <Cell icon={<span className="ava">{user.short}</span>} title={user.name} sub={`${user.role} · ${what}`} /> : null
}

/** Все окна сверки карточки ЖК: заполнение, проверка по-русски, запись на сервер, ошибки сервера — в окне */
export function VerifyDialogs({ c, dlg, setDlg, onSaved }: { c: Complex; dlg: Dlg | null; setDlg: (d: Dlg | null) => void; onSaved?: (msg: string) => void }) {
  const store = useStore()
  const { user } = store
  const fileRef = useRef<HTMLInputElement>(null)
  if (!dlg) return null
  const close = () => setDlg(null)
  const today = todayLocal()
  const fail = (e: unknown) => setDlg({ ...dlg, busy: false, err: errorText(e) })
  const run = (errors: FormErrors, send: () => Promise<unknown>, msg: string) => {
    if (dlg.busy) return
    if (Object.keys(errors).length) { setDlg({ ...dlg, errors, err: undefined }); return }
    setDlg({ ...dlg, busy: true, err: undefined, errors: {} })
    send().then(() => { setDlg(null); onSaved?.(msg) }, fail)
  }
  const errs = dlg.errors ?? {}
  const err = dlg.err && <div className="alog-err t-sm" style={{ margin: '8px 24px 0' }}>Не сохранено: {dlg.err}</div>
  const footer = (label: string, submit: () => void, cls = 'y') => (
    <><button className="btn g" onClick={close}>Отмена</button><button className={`btn ${cls}`} disabled={dlg.busy} onClick={submit}>{dlg.busy ? 'Записываем…' : label}</button></>
  )

  if (dlg.kind === 'confirm') {
    const lines = confirmLines(dlg.doc, dlg.sg, user?.name ?? '—', today)
    const submit = () => run({}, () => store.verifyBasis(c.id, confirmBody(dlg.doc, dlg.sg, '', today)), `${targetTitle(dlg.doc)}: сверено — есть`)
    return (
      <Modal title="Подтвердить по источнику" onClose={close}
        footer={<><button className="btn g" onClick={() => setDlg({ kind: 'check', target: dlg.doc, form: { ...initialForm(c, dlg.doc), value: 'yes' } })}>Изменить данные</button>{footer('Записать: есть', submit)}</>}>
        <p className="t-sm" style={{ padding: '0 24px 12px', lineHeight: '20px' }}>Документ совпадает с открытым реестром ({dlg.sg.sourceName}). Будет записано:</p>
        <div className="kv vf-kv" style={{ margin: '0 24px' }}>
          {lines.flatMap(([k, v]) => [<span key={`${k}k`}>{k}</span>, <span key={`${k}v`}>{k === 'Ссылка' ? <SrcLink url={v}>{v}</SrcLink> : v}</span>])}
        </div>
        <p className="t-xs c2" style={{ padding: '12px 24px 0', lineHeight: '17px' }}>Сверка попадёт в журнал объекта с вашим именем и временем. Риск и «Проверка по рекламе» пересчитаются сразу.</p>
        {err}
      </Modal>
    )
  }

  if (dlg.kind === 'check') {
    const t = dlg.target, f = dlg.form
    const set = (patch: Partial<CheckForm>) => setDlg({ ...dlg, form: { ...f, ...patch }, err: undefined, errors: {} })
    const sg = t === 'list' ? null : c.basisSuggest?.[t] ?? null
    const rec = checkOf(c, t)
    const submit = () => run(validateCheck(f, today), () => (t === 'list'
      ? store.checkPublishedList(c.id, checkBody(t, f, ''))
      : store.verifyBasis(c.id, checkBody(t, f, ''))), `${targetTitle(t)}: сверка записана`)
    return (
      <Modal title={`Сверка: ${targetTitle(t)}`} onClose={close} footer={footer(f.value === 'unknown' && rec ? 'Снять сверку' : 'Записать сверку', submit)}>
        {sg && (
          <div className="box od-note t-sm" style={{ margin: '0 24px 12px' }}>
            <IcDoc size={18} />
            <div>Открытый реестр ({sg.sourceName}): <b>{sg.ref}</b>{sg.url && <> · <SrcLink url={sg.url}>открыть источник</SrcLink></>}. Сведения источника — сравните с документом и отметьте результат.</div>
          </div>
        )}
        <div style={{ padding: '0 24px' }}>
          <Row label={t === 'list' ? 'Объект в перечне' : 'Документ'} error={errs.value}>
            <Choice value={f.value} items={TRI_ITEMS} label="Результат сверки" onChange={(v) => set({ value: v })} />
            {f.value === 'unknown' && <div className="t-xs c2" style={{ marginTop: 6 }}>{rec ? 'Сверка будет снята: документ снова «не проверялся».' : 'Источник необязателен: отметка «нет сведений» не считается сверкой.'}</div>}
          </Row>
          {t !== 'list' && (
            <Row label={t === 'bvu' ? 'Банк или документ' : 'Номер и дата'} htmlFor="vf-ref" error={errs.ref}>
              <input id="vf-ref" className="input" maxLength={500} value={f.ref} placeholder={t === 'bvu' ? 'Например: АО «Банк Восток», договор № 12 от 01.03.2026' : 'Например: № KZ40VWE00002526 от 15.07.2025'} onChange={(e) => set({ ref: e.target.value })} />
            </Row>
          )}
          <Row label="Где проверено" htmlFor="vf-src" error={errs.source}>
            <SourcePick id="vf-src" kind={t === 'list' ? 'list' : 'basis'} choice={f.choice} other={f.other} onChange={(p) => set(p)} />
          </Row>
          <Row label="Ссылка" htmlFor="vf-url" error={errs.url}>
            <input id="vf-url" className="input" type="url" inputMode="url" maxLength={512} value={f.url} placeholder="https://… — необязательно" onChange={(e) => set({ url: e.target.value })} />
          </Row>
          <Row label="Дата сверки" htmlFor="vf-date" error={errs.date}>
            <input id="vf-date" className="input" type="date" max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} />
          </Row>
          <Row label="Комментарий" htmlFor="vf-com" error={errs.comment} top>
            <textarea id="vf-com" className="input" style={{ minHeight: 72 }} maxLength={2000} value={f.comment} placeholder="Необязательно: что видно в источнике, кому направлен запрос" onChange={(e) => set({ comment: e.target.value })} />
          </Row>
        </div>
        {err}
        <Who user={user} what="сверил — имя, дата и время попадут в журнал" />
      </Modal>
    )
  }

  if (dlg.kind === 'readiness') {
    const f = dlg.form, which = dlg.which
    const set = (patch: Partial<ReadinessForm>) => setDlg({ ...dlg, form: { ...f, ...patch }, err: undefined, errors: {} })
    const submit = () => run(validateReadiness(f, today), () => store.setReadiness(c.id, readinessBody(which, f, '')), 'Готовность записана')
    return (
      <Modal title={which === 'declared' ? 'Готовность: заявлено застройщиком' : 'Готовность: подтверждено'} onClose={close} footer={footer('Записать готовность', submit)}>
        <p className="t-sm c2" style={{ padding: '0 24px 8px', lineHeight: '20px' }}>{which === 'declared'
          ? 'Что застройщик пишет о готовности: сайт, реклама, Homeportal.'
          : 'Готовность по проверке: акт осмотра, выезд, фото-мониторинг. Разница с заявленной от 20 п.п. — фактор R5.'}</p>
        <div style={{ padding: '0 24px' }}>
          <Row label="Готовность, %" htmlFor="vr-val" error={errs.value}>
            <input id="vr-val" className="input num" inputMode="decimal" maxLength={6} value={f.value} placeholder="от 0 до 100" onChange={(e) => set({ value: e.target.value })} />
          </Row>
          <Row label="Источник" htmlFor="vr-src" error={errs.source}>
            <SourcePick id="vr-src" kind={which} choice={f.choice} other={f.other} onChange={(p) => set(p)} />
          </Row>
          <Row label="На дату" htmlFor="vr-date" error={errs.date}>
            <input id="vr-date" className="input" type="date" max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} />
          </Row>
          <Row label="Комментарий" htmlFor="vr-com" error={errs.comment} top>
            <textarea id="vr-com" className="input" style={{ minHeight: 72 }} maxLength={2000} value={f.comment} placeholder="Необязательно: номер акта, что видно на площадке" onChange={(e) => set({ comment: e.target.value })} />
          </Row>
        </div>
        {err}
        <Who user={user} what="внёс — имя, дата и время попадут в журнал" />
      </Modal>
    )
  }

  if (dlg.kind === 'appeal') {
    const f = dlg.form
    const set = (patch: Partial<AppealForm>) => setDlg({ ...dlg, form: { ...f, ...patch }, err: undefined, errors: {} })
    const submit = () => run(validateAppeal(f, today), () => store.addAppeal(c.id, appealBody(f, '')), 'Обращение внесено — ждёт проверки')
    return (
      <Modal title="Добавить обращение" onClose={close} footer={footer('Внести обращение', submit)}>
        <p className="t-sm c2" style={{ padding: '0 24px 8px', lineHeight: '20px' }}>Обращение попадёт в список «на проверке». Коллективное проверенное обращение — фактор R4.</p>
        <div style={{ padding: '0 24px' }}>
          <Row label="Дата обращения" htmlFor="va-date" error={errs.date}>
            <input id="va-date" className="input" type="date" max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} />
          </Row>
          <Row label="Суть обращения" htmlFor="va-subj" error={errs.subject} top>
            <textarea id="va-subj" className="input" style={{ minHeight: 72 }} maxLength={2000} value={f.subject} placeholder="Например: внесли первый взнос, договор долевого участия не зарегистрирован" onChange={(e) => set({ subject: e.target.value })} />
          </Row>
          <Row label="Заявителей" htmlFor="va-n" error={errs.applicants}>
            <div className="row" style={{ gap: 16 }}>
              <input id="va-n" className="input num" style={{ width: 120 }} inputMode="numeric" maxLength={6} value={f.applicants} onChange={(e) => set({ applicants: e.target.value.replace(/\D/g, '') })} />
              <label className="row t-sm" style={{ gap: 8, cursor: 'pointer' }}><input type="checkbox" checked={f.collective} onChange={(e) => set({ collective: e.target.checked })} />Коллективное</label>
            </div>
          </Row>
          <Row label="Откуда" htmlFor="va-src" error={errs.source}>
            <SourcePick id="va-src" kind="appeals" choice={f.choice} other={f.other} onChange={(p) => set(p)} />
          </Row>
          <Row label="Комментарий" htmlFor="va-com" error={errs.comment} top>
            <textarea id="va-com" className="input" style={{ minHeight: 64 }} maxLength={2000} value={f.comment} placeholder="Необязательно: входящий номер, контакт" onChange={(e) => set({ comment: e.target.value })} />
          </Row>
        </div>
        {err}
        <Who user={user} what="внёс — имя, дата и время попадут в журнал" />
      </Modal>
    )
  }

  if (dlg.kind === 'decide') {
    const a = dlg.appeal, verify = dlg.action === 'verify'
    const submit = () => run({}, () => store.decideAppeal(a.id, dlg.action, dlg.comment), verify ? 'Обращение подтверждено' : 'Обращение отклонено')
    return (
      <Modal title={verify ? 'Подтвердить обращение' : 'Отклонить обращение'} onClose={close} footer={footer(verify ? 'Подтвердить' : 'Отклонить', submit, verify ? 'y' : 'd')}>
        <Cell icon={<span className="ico"><IcUsers size={18} /></span>} title={a.subject} sub={[a.collective ? 'Коллективное' : 'Индивидуальное', `заявителей: ${a.applicants}`, a.source].join(' · ')} />
        <p className="t-sm c2" style={{ padding: '4px 24px 8px', lineHeight: '20px' }}>{verify
          ? (a.collective ? 'Проверенное коллективное обращение — фактор R4 (+15 баллов).' : 'Проверенное обращение — проверенные сведения по объекту; R4 дают только коллективные.')
          : 'Отклонённое обращение в риске не учитывается.'}</p>
        <div style={{ padding: '0 24px 4px' }}>
          <label className="t-sm c2" htmlFor="vd-com">Основание решения — необязательно</label>
          <textarea id="vd-com" className="input" style={{ marginTop: 6, minHeight: 72 }} maxLength={2000} value={dlg.comment}
            placeholder={verify ? 'Например: сверено с ДЭР, вх. № ЗТ-1184' : 'Например: заявители — покупатели в сданном доме'} onChange={(e) => setDlg({ ...dlg, comment: e.target.value, err: undefined })} />
        </div>
        {err}
        <Who user={user} what="решение, дата и время попадут в журнал" />
      </Modal>
    )
  }

  if (dlg.kind === 'appealsCheck') {
    const f = dlg.form
    const set = (patch: Partial<AppealsCheckForm>) => setDlg({ ...dlg, form: { ...f, ...patch }, err: undefined, errors: {} })
    const submit = () => run(validateAppealsCheck(f, today), () => store.checkAppeals(c.id, appealsCheckBody(f, '')), 'Записано: обращений нет — сверено')
    return (
      <Modal title="Обращений нет — сверено" onClose={close} footer={footer('Записать', submit)}>
        <p className="t-sm c2" style={{ padding: '0 24px 8px', lineHeight: '20px' }}>Пустой список в системе не значит, что обращений нет. Отметьте, где проверили — тогда фактор R4 будет «обращений нет», а не «нужна проверка».</p>
        <div style={{ padding: '0 24px' }}>
          <Row label="Где проверено" htmlFor="vc-src" error={errs.source}>
            <SourcePick id="vc-src" kind="appeals" choice={f.choice} other={f.other} onChange={(p) => set(p)} />
          </Row>
          <Row label="Дата сверки" htmlFor="vc-date" error={errs.date}>
            <input id="vc-date" className="input" type="date" max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} />
          </Row>
          <Row label="Комментарий" htmlFor="vc-com" top>
            <textarea id="vc-com" className="input" style={{ minHeight: 64 }} maxLength={2000} value={f.comment} placeholder="Необязательно: номер ответа на запрос" onChange={(e) => set({ comment: e.target.value })} />
          </Row>
        </div>
        {err}
        <Who user={user} what="сверил — имя, дата и время попадут в журнал" />
      </Modal>
    )
  }

  // снимок площадки
  const f = dlg.form
  const set = (patch: Partial<PhotoForm>) => setDlg({ ...dlg, form: { ...f, ...patch }, err: undefined, errors: {} })
  const submit = () => run(validatePhoto(f, today), () => store.addSitePhoto(c.id, f.file!, photoFields(f, '')), 'Снимок площадки добавлен')
  return (
    <Modal title="Добавить снимок площадки" onClose={close} footer={footer('Загрузить снимок', submit)}>
      <p className="t-sm c2" style={{ padding: '0 24px 8px', lineHeight: '20px' }}>Датированные снимки подтверждают ход работ или простой (фактор R6: от 90 дней, не меньше двух снимков без активности).</p>
      <div style={{ padding: '0 24px' }}>
        <Row label="Снимок" error={errs.file}>
          <div className="row" style={{ gap: 12 }}>
            <button type="button" className="btn g m" onClick={() => fileRef.current?.click()}><IcCamera size={18} />{f.file ? 'Другой файл' : 'Выбрать файл'}</button>
            <span className="t-sm c2 vf-file">{f.file ? f.file.name : 'JPEG, PNG, WEBP или GIF до 10 МБ'}</span>
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => { set({ file: e.target.files?.[0] ?? null }); e.target.value = '' }} />
          </div>
        </Row>
        <Row label="Дата съёмки" htmlFor="vp-date" error={errs.date}>
          <input id="vp-date" className="input" type="date" max={today} value={f.date} onChange={(e) => set({ date: e.target.value })} />
        </Row>
        <Row label="Откуда снимок" htmlFor="vp-src" error={errs.source}>
          <SourcePick id="vp-src" kind="photo" choice={f.choice} other={f.other} onChange={(p) => set(p)} />
        </Row>
        <Row label="На площадке" error={errs.activity}>
          <Choice value={f.activity} items={ACTIVITIES} label="Активность на площадке" onChange={(v) => set({ activity: v })} />
        </Row>
        <Row label="Примечание" htmlFor="vp-note" error={errs.comment} top>
          <textarea id="vp-note" className="input" style={{ minHeight: 64 }} maxLength={2000} value={f.note} placeholder="Необязательно: например, кран стоит, ограждения нет" onChange={(e) => set({ note: e.target.value })} />
        </Row>
      </div>
      {err}
      <Who user={user} what="загрузил — имя, дата и время попадут в журнал" />
    </Modal>
  )
}

/** Кнопка добавления (готовность, обращение, снимок): в офлайн-копии — недоступна с пояснением */
export function AddButton({ live, onClick, children, cls = 'g' }: { live: boolean; onClick: () => void; children: ReactNode; cls?: string }) {
  return (
    <span className="vf-add">
      <button type="button" className={`btn ${cls} s`} disabled={!live} title={live ? undefined : OFFLINE_HINT} onClick={onClick}><IcPlus size={14} />{children}</button>
      {!live && <span className="t-xs c2">{OFFLINE}</span>}
    </span>
  )
}
