import { coverSrc } from '../lib/gallery'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Cell, Faq, Hero, Modal, RiskScore, Seg, fmtDT } from '../components/ui'
import { AdminModal } from '../components/Connectors'
import type { Complex, Zone } from '../data/types'
import { isAdmin, onAdminChange } from '../lib/admin'
import {
  STATUS_TONE, TEXT_MAX, approveAlert, approveProblem, audienceLine, audienceProblems, canRetry, checkBot, createAlert,
  deliveryLine, footer, getAlerts, getAudience, rejectAlert, retryAlert, type Audience, type ResidentAlert,
} from '../lib/alerts'
import { errorText } from '../lib/api'
import { isAssessed, model } from '../lib/risk'
import { useStore } from '../lib/store'
import { IcCheck, IcChevronDown, IcClose, IcExternal, IcLock, IcRefresh, IcSend, IcShield } from '../ui/icons'
import { Board, Cap, Kpi, Row, Shares } from '../components/StatBoard'

const T = model.thresholds
type Tpl = 'basis' | 'idle' | 'ok'
interface TplDef { label: string; title: string; text: (c: Complex) => string; fits: (c: Complex) => boolean; why: string }
// Шаблоны не утверждают нарушение: только проверяемый факт по объекту и совет жителю. fits — факт есть в данных объекта
const TPL: Record<Tpl, TplDef> = {
  basis: {
    label: 'Проверьте основание', title: 'Проверьте основание перед оплатой',
    text: (c) => `Перед бронью или взносом в ЖК «${c.name}» попросите у застройщика номер гарантии ЕО или разрешения МИО и сверьте его в ГАСК. Не вносите деньги без договора с этими реквизитами.`,
    fits: (c) => c.stage !== model.builtStage, why: 'объект уже введён в эксплуатацию',
  },
  idle: {
    label: 'Стройка стоит', title: 'Стройка приостановлена',
    text: (c) => `По снимкам на площадке ЖК «${c.name}» нет работ ${c.idleDays} дн. Уточните сроки у застройщика, прежде чем вносить деньги.`,
    fits: (c) => c.stage !== model.builtStage && c.idleDays >= T.idleDays
      && c.photos.filter((p) => p.date && T.idleActivities.includes(p.activity)).length >= T.idlePhotosMin,
    why: `простой от ${T.idleDays} дн. не подтверждён снимками`,
  },
  ok: {
    label: 'Есть в перечне', title: 'Объект в перечне ГАСК',
    text: (c) => `ЖК «${c.name}» есть в опубликованном перечне ГАСК. Договор долевого участия заключайте только с номером гарантии ЕО или разрешения МИО.`,
    fits: (c) => c.inPublishedList === 'yes', why: 'объекта нет в опубликованном перечне ГАСК или перечень не сверен',
  },
}
const TPLS = Object.keys(TPL) as Tpl[]
const firstFit = (c: Complex, cur?: Tpl): Tpl => (cur && TPL[cur].fits(c) ? cur : TPLS.find((k) => TPL[k].fits(c)) ?? 'basis')
const NOW = new Date().toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })
// пока идёт отправка, список перечитывается — статус доставки обновляет воркер
const POLL_MS = 4000

/** Жителям — конструктор предупреждения с превью в Telegram, согласование другим сотрудником, отправка и её итог */
// пока ЖК не загружены (прямой переход на /residents), выбирать объект не из чего — страница ждёт данных
export default function Residents() {
  const { complexes } = useStore()
  return complexes.length ? <ResidentsPage /> : <div className="empty">Загружаем объекты…</div>
}

function ResidentsPage() {
  const { complexes, riskOf, user, mode, notify, revision } = useStore()
  const live = mode === 'api'
  const scored = useMemo(() => complexes.map((c) => ({ c, r: riskOf(c) })).sort((a, b) => b.r.score - a.r.score), [complexes, riskOf])
  const [cid, setCid] = useState(scored[0].c.id)
  const [pick, setPick] = useState(false)
  const [tpl, setTpl] = useState<Tpl>(() => firstFit(scored[0].c))
  const [msg, setMsg] = useState('')
  const [alerts, setAlerts] = useState<ResidentAlert[] | null>(null)
  const [aud, setAud] = useState<Audience | null>(null)
  const [ver, setVer] = useState(0)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string>()
  const [admin, setAdmin] = useState(() => isAdmin())
  const [adminOpen, setAdminOpen] = useState(false)
  useEffect(() => onAdminChange(setAdmin), [])

  const cur = scored.find((x) => x.c.id === cid)!
  const c = cur.c
  const district = (c.district || '').trim()
  const title = TPL[tpl].title
  const text = msg || TPL[tpl].text(c)
  const fits = TPL[tpl].fits(c)
  const count = (z: Zone) => scored.filter((x) => isAssessed(x.r) && x.r.zone === z).length
  const fitN = (k: Tpl) => scored.filter((x) => TPL[k].fits(x.c)).length
  /** Плитка шаблона: выбрать его; если к текущему объекту он не подходит — самый рискованный объект, к которому подходит */
  const pickTpl = (k: Tpl) => {
    const to = TPL[k].fits(c) ? c : scored.find((x) => TPL[k].fits(x.c))?.c
    if (to) setCid(to.id)
    setTpl(k); setMsg('')
  }

  // список предупреждений и получатели района — только с сервером; офлайн-копия показывает конструктор и превью
  useEffect(() => {
    if (!live) return
    let alive = true
    getAlerts().then((x) => { if (alive && x) setAlerts(x) }, () => {})
    return () => { alive = false }
  }, [live, ver, revision])
  useEffect(() => {
    if (!live) return
    let alive = true
    getAudience(district).then((x) => { if (alive) setAud(x) }, () => { if (alive) setAud(null) })
    return () => { alive = false }
  }, [live, district, revision])
  const sending = !!alerts?.some((a) => a.status === 'sending')
  useEffect(() => {
    if (!sending) return
    const t = setTimeout(() => setVer((v) => v + 1), POLL_MS)
    return () => clearTimeout(t)
  }, [sending, alerts])

  const put = (a: ResidentAlert) => setAlerts((xs) => [a, ...(xs ?? []).filter((x) => x.id !== a.id)].sort((x, y) => y.id - x.id))
  const pendingHere = alerts?.find((a) => a.complexId === c.id && a.status === 'pending')
  const whom = aud ? audienceLine(aud) : null
  const problems = audienceProblems(aud)
  const why = !live ? 'Офлайн-копия: отправка — только с сервером (./dev.sh)'
    : !user ? 'Войдите, чтобы подготовить предупреждение'
      : !fits ? 'Шаблон не подходит к объекту'
        : !text.trim() ? 'Введите текст'
          : pendingHere ? `По этому ЖК уже есть предупреждение на согласовании (${pendingHere.code})`
            : null

  const submit = async () => {
    if (why || busy || !user) return
    setBusy(true)
    setErr(undefined)
    try {
      const got = await createAlert({ complexId: c.id, template: tpl, title, text: text.trim(), author: user.name })
      put(got)
      setMsg('')
      notify(`Предупреждение ${got.code} отправлено на согласование`)
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const st = (s: ResidentAlert['status']) => alerts?.filter((a) => a.status === s).length ?? 0

  return (
    <>
      <Hero crumb="Жителям" title="Предупредить жителей"
        info={<span><b className="bold">Предупреждение уходит только после согласования.</b> Аналитик готовит текст по данным объекта, другой сотрудник в режиме администратора согласует — и сообщение уходит в Telegram: в канал ГАСК и подписчикам района в боте заявок</span>}
        board={<Board kind="residents">
          <Kpi big em="flag" tone="red" value={count('red') + count('yellow')} label="ЖК можно предупредить" hint="красная и жёлтая зоны"
            onClick={() => { setCid(scored[0].c.id); setTpl(firstFit(scored[0].c, tpl)); setMsg('') }} />
          <div className="sb-col">
            <Cap aside="только оценённые ЖК">Кого касается</Cap>
            <Shares parts={[
              { key: 'red', label: 'Красная зона', value: count('red'), tone: 'red', to: '/complexes?zone=red' },
              { key: 'yellow', label: 'Жёлтая зона', value: count('yellow'), tone: 'yellow', to: '/complexes?zone=yellow' },
            ]} />
          </div>
          <div className="sb-sep" />
          <div className="sb-col">
            <Cap>Шаблоны · подходят к ЖК</Cap>
            <Row em="docs" label="«Проверьте основание»" value={fitN('basis')} onClick={() => pickTpl('basis')} active={tpl === 'basis'} />
            <Row em="hourglass" label="«Стройка стоит»" value={fitN('idle')} onClick={() => pickTpl('idle')} active={tpl === 'idle'} />
            <Row em="city" label="«Есть в перечне»" value={fitN('ok')} onClick={() => pickTpl('ok')} active={tpl === 'ok'} />
          </div>
          {alerts && <>
            <div className="sb-sep" />
            <div className="sb-col">
              <Cap aside="из этого развёртывания">Предупреждения</Cap>
              <Row em="hourglass" label="На согласовании" value={st('pending')} />
              <Row em="docs" label="Отправлено" value={st('sent') + st('partial')} />
            </div>
          </>}
        </Board>}>
        <div className="panel" style={{ gridTemplateColumns: 'minmax(0, 1fr) 380px' }}>
          <div className="pl">
            <h2 className="t-h2" style={{ marginBottom: 12 }}>Предупреждение</h2>
            <div className="frow"><label>Объект</label>
              <button className="input row between" onClick={() => setPick(true)} style={{ textAlign: 'left' }}><span className="row" style={{ gap: 8 }}>ЖК «{c.name}»<RiskScore r={cur.r} /></span><IcChevronDown size={16} /></button>
            </div>
            <div className="frow"><label>Шаблон</label><Seg value={tpl} onChange={(v) => { setTpl(v); setMsg('') }} items={TPLS.map((k) => [k, TPL[k].label])} /></div>
            {!fits && <div className="frow"><span /><span className="t-sm" style={{ color: 'var(--amber-ink)' }}>Шаблон не подходит к объекту: {TPL[tpl].why}. Выберите другой.</span></div>}
            <div className="frow top"><label>Текст</label>
              <div><textarea className="input" maxLength={TEXT_MAX} value={text} onChange={(e) => setMsg(e.target.value)} /><div className="t-xs c2" style={{ marginTop: 4, textAlign: 'right' }}>{text.length} / {TEXT_MAX}</div></div>
            </div>
            <div className="frow"><label>Куда</label>
              <span>Telegram{aud?.channel ? <> · канал <b className="bold">{aud.channel}</b></> : ''} · подписчики района в боте<span className="c2"> · push в приложении — после пилота</span></span>
            </div>
            <div className="frow top"><label>Кому</label>
              <div>
                {!live ? <span className="c2">Получатели видны при работе с сервером</span>
                  : !aud ? <span className="c2">Получатели не загружены</span>
                    : whom ? <span>{whom}{aud.everywhere ? <span className="c2"> · из них подписаны на все районы: {aud.everywhere}</span> : null}</span>
                      : <span className="c2">Получателей пока нет</span>}
                {problems.map((p) => <div key={p} className="t-sm" style={{ color: 'var(--amber-ink)', marginTop: 4 }}>{p}</div>)}
              </div>
            </div>
            <div className="frow"><span /><div className="row wrap-f" style={{ gap: 12 }}>
              <button className="btn y" disabled={!!why || busy} title={why ?? undefined} onClick={submit}><IcSend />{busy ? 'Отправляем…' : 'Отправить на согласование'}</button>
              <span className="t-sm c2">{why ?? 'Жителям уйдёт только после согласования другим сотрудником'}</span>
            </div></div>
            {err && <div className="frow"><span /><span className="t-sm" style={{ color: 'var(--red)' }}>{err}</span></div>}
          </div>
          <div className="pr" style={{ alignItems: 'center', justifyContent: 'center', padding: 20, gap: 10 }}>
            <span className="t-sm c2">Как увидит житель в Telegram</span>
            <div className="phone" style={{ width: 260, height: 530, boxShadow: 'none' }}>
              <div className="phone-scr">
                <div style={{ textAlign: 'center', color: '#fff', paddingTop: 44, textShadow: '0 1px 10px rgba(0,0,0,.4)' }}>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{NOW}</div>
                  <div style={{ fontSize: 64, fontWeight: 600, lineHeight: 1 }}>09:41</div>
                </div>
                <div className="notif fade" key={text + tpl} style={{ marginTop: 24 }}>
                  <div className="row" style={{ gap: 8, marginBottom: 4 }}>
                    <span style={{ width: 20, height: 20, borderRadius: 6, background: '#26A5E4', color: '#fff', display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 700 }}>T</span>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{aud?.channel ?? 'Telegram · ГАСК ВКО'}</span>
                    <span className="c2" style={{ marginLeft: 'auto', fontSize: 12 }}>сейчас</span>
                  </div>
                  <div style={{ fontWeight: 600 }}>{title}</div>
                  <div style={{ marginTop: 2 }}>{text}</div>
                  <div className="c2" style={{ marginTop: 6, fontSize: 12 }}>{footer(district)}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Hero>

      {live && <AlertList alerts={alerts} admin={admin} configured={!!aud?.adminConfigured} onAdmin={() => setAdminOpen(true)}
        onChange={(a) => { put(a); setVer((v) => v + 1) }} />}

      <Faq title="Вопросы о предупреждениях"
        items={[
          ['Кто решает, что отправить?', 'Аналитик готовит предупреждение по данным объекта, согласует другой сотрудник в режиме администратора — автор своё предупреждение согласовать не может. Автоматически система жителям ничего не отправляет.'],
          ['Куда уходит сообщение?', 'В публичный канал ГАСК в Telegram (с хэштегом района) и личным сообщением подписчикам района в боте заявок. Получатели фиксируются в момент согласования.'],
          ['Какие данные о жителях хранятся?', 'Только номер чата Telegram и выбранный район — без имени, логина и телефона. После доставки номер чата из записи о доставке стирается; житель, заблокировавший бота, отписывается автоматически.'],
          ['Что если отправка не прошла?', 'Итог виден в списке: сколько доставлено, кому нет и почему. Если Telegram ограничил частоту или не было связи, воркер повторит сам; после исправления токена бота — «Повторить отправку». Дважды одно предупреждение житель не получит.'],
          ['Почему не только Instagram?', 'Пост в ленте легко пропустить, а реклама ЖК приходит адресно. Сообщение по району доходит до тех, кто живёт рядом со стройкой.'],
        ]} />

      {pick && (
        <Modal title="Выберите объект" onClose={() => setPick(false)}>
          {scored.map(({ c: x, r }) => <Cell key={x.id} inset icon={<img className="th" src={coverSrc(x)} alt="" style={{ width: 44, height: 44, borderRadius: 12 }} />} title={x.name} sub={x.address}
            end={<><RiskScore r={r} />{x.id === cid && <IcCheck size={18} style={{ color: 'var(--text)' }} />}</>} onClick={() => { setCid(x.id); setTpl(firstFit(x, tpl)); setMsg(''); setPick(false) }} />)}
        </Modal>
      )}
      {adminOpen && <AdminModal onClose={() => setAdminOpen(false)} />}
    </>
  )
}

/** Предупреждения: статус, кто подготовил и согласовал, итог доставки; на согласовании — «Согласовать и отправить» и «Отклонить» */
function AlertList({ alerts, admin, configured, onAdmin, onChange }: {
  alerts: ResidentAlert[] | null; admin: boolean; configured: boolean; onAdmin: () => void; onChange: (a: ResidentAlert) => void
}) {
  const { notify, user } = useStore()
  const [check, setCheck] = useState<{ busy?: boolean; text?: string; ok?: boolean }>({})
  const runCheck = async () => {
    setCheck({ busy: true })
    try {
      const got = await checkBot()
      setCheck({ text: got.detail, ok: got.ok })
    } catch (e) {
      setCheck({ text: errorText(e), ok: false })
    }
  }
  return (
    <section style={{ marginTop: 32 }}>
      <div className="row between" style={{ marginBottom: 12, flexWrap: 'wrap', gap: 12 }}>
        <h2 className="t-h2">Предупреждения</h2>
        <div className="row wrap-f" style={{ gap: 8 }}>
          {admin && <button className="btn g m" disabled={check.busy} onClick={runCheck}><IcShield size={16} />{check.busy ? 'Проверяем…' : 'Проверить бота и канал'}</button>}
          {!admin && configured && <button className="btn d m" onClick={onAdmin}><IcLock size={16} />Режим администратора</button>}
        </div>
      </div>
      {check.text && <div className={`ai-note${check.ok ? '' : ' warn'}`} style={{ marginBottom: 12 }}>{check.text}</div>}
      {!alerts ? <div className="empty">Загружаем предупреждения…</div>
        : !alerts.length ? <div className="empty">Предупреждений ещё не было. Подготовьте первое в форме выше — оно уйдёт после согласования.</div>
          : <div style={{ display: 'grid', gap: 12 }}>{alerts.map((a) => <AlertCard key={a.id} a={a} admin={admin} who={user?.name ?? null} notify={notify} onChange={onChange} onAdmin={onAdmin} configured={configured} />)}</div>}
    </section>
  )
}

function AlertCard({ a, admin, who, notify, onChange, onAdmin, configured }: {
  a: ResidentAlert; admin: boolean; who: string | null; notify: (t: string) => void; onChange: (a: ResidentAlert) => void
  onAdmin: () => void; configured: boolean
}) {
  const [busy, setBusy] = useState<'approve' | 'reject' | 'retry' | null>(null)
  const [err, setErr] = useState<string>()
  const [comment, setComment] = useState('')
  const problem = approveProblem(a, who, admin)
  const run = async (what: 'approve' | 'reject' | 'retry') => {
    if (!who || busy) return
    setBusy(what)
    setErr(undefined)
    try {
      const got = what === 'approve' ? await approveAlert(a.id, who, comment)
        : what === 'reject' ? await rejectAlert(a.id, who, comment) : await retryAlert(a.id, who)
      onChange(got)
      setComment('')
      notify(what === 'approve' ? `${a.code}: согласовано — отправка началась` : what === 'reject' ? `${a.code}: отклонено` : `${a.code}: повторная отправка началась`)
    } catch (e) {
      setErr(errorText(e))
    } finally {
      setBusy(null)
    }
  }
  const line = deliveryLine(a)
  return (
    <div className="card card-pad">
      <div className="row between" style={{ gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div style={{ minWidth: 0 }}>
          <div className="row wrap-f" style={{ gap: 8 }}>
            <b className="bold">{a.code}</b>
            <span className={`tag ${STATUS_TONE[a.status]}`}>{a.statusLabel}</span>
            <Link to={`/complexes/${a.complexId}`} className="link">ЖК «{a.complexName}»</Link>
            {a.district && <span className="t-sm c2">{a.district}</span>}
          </div>
          <div style={{ marginTop: 8, fontWeight: 600 }}>{a.title}</div>
          <div style={{ marginTop: 2 }}>{a.text}</div>
          <div className="t-sm c2" style={{ marginTop: 8 }}>
            Подготовил: {a.createdBy}{a.createdAt ? `, ${fmtDT(a.createdAt)}` : ''}
            {a.decidedBy && <> · {a.status === 'rejected' ? 'отклонил' : 'согласовал'}: {a.decidedBy}{a.decidedAt ? `, ${fmtDT(a.decidedAt)}` : ''}</>}
            {a.decisionComment && <> · «{a.decisionComment}»</>}
          </div>
          {line && <div className="t-sm" style={{ marginTop: 4 }}>{line}{a.channel?.link && <> · <a className="link" href={a.channel.link} target="_blank" rel="noreferrer">в канале<IcExternal size={12} /></a></>}</div>}
          {a.channel?.error && <div className="t-sm" style={{ marginTop: 4, color: 'var(--amber-ink)' }}>Канал: {a.channel.error}</div>}
          {a.errors.map((e) => <div key={e.text} className="t-sm" style={{ marginTop: 4, color: 'var(--amber-ink)' }}>{e.text}: {e.count}</div>)}
          {a.lastError && a.status === 'sending' && <div className="t-sm c2" style={{ marginTop: 4 }}>{a.lastError}</div>}
          {a.lastError && a.status === 'failed' && !a.channel?.error && !a.errors.length && <div className="t-sm" style={{ marginTop: 4, color: 'var(--red)' }}>{a.lastError}</div>}
        </div>
      </div>
      {a.status === 'pending' && (
        <div style={{ marginTop: 12 }}>
          <input className="input" placeholder="Комментарий к решению (необязательно)" value={comment} maxLength={2000} onChange={(e) => setComment(e.target.value)} />
          <div className="row wrap-f" style={{ gap: 8, marginTop: 8 }}>
            <button className="btn y m" disabled={!!problem || !!busy} title={problem ?? undefined} onClick={() => run('approve')}><IcSend size={16} />{busy === 'approve' ? 'Согласуем…' : 'Согласовать и отправить'}</button>
            <button className="btn g m" disabled={!admin || !who || !!busy} title={!admin ? 'Отклонить — в режиме администратора' : undefined} onClick={() => run('reject')}><IcClose size={16} />{busy === 'reject' ? 'Отклоняем…' : 'Отклонить'}</button>
            {problem && <span className="t-sm c2">{problem}</span>}
            {!admin && configured && <button className="btn d s" onClick={onAdmin}><IcLock size={14} />Войти</button>}
          </div>
        </div>
      )}
      {canRetry(a) && (
        <div className="row wrap-f" style={{ gap: 8, marginTop: 12 }}>
          <button className="btn g m" disabled={!admin || !who || !!busy} title={!admin ? 'Повтор — в режиме администратора' : undefined} onClick={() => run('retry')}><IcRefresh size={16} />{busy === 'retry' ? 'Запускаем…' : 'Повторить отправку'}</button>
          <span className="t-sm c2">Уйдёт только тем, кому ещё не отправлено</span>
        </div>
      )}
      {err && <div className="t-sm" style={{ marginTop: 8, color: 'var(--red)' }}>{err}</div>}
    </div>
  )
}
