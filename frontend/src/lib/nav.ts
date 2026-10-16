// «Откуда пришёл»: ссылка передаёт в state подпись и адрес, страница показывает их в крошках и как «← назад»
import { useLocation } from 'react-router-dom'

export interface From { label: string; to: string }

/** state для <Link>: <Link to="/ads/a1" state={from('Сигнал S-0001', '/signals/1')}> */
export const from = (label: string, to: string) => ({ from: { label, to } satisfies From })

/** Откуда пришли на страницу (если ссылка передала from) */
export function useFrom(): From | undefined {
  const st = useLocation().state as { from?: Partial<From> } | null
  const f = st?.from
  // только внутренние адреса приложения — без открытых редиректов
  return f && typeof f.label === 'string' && typeof f.to === 'string' && f.to.startsWith('/') && !f.to.startsWith('//')
    ? { label: f.label, to: f.to } : undefined
}
