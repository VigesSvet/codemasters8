// Узкий экран телефона (как @media (max-width: 760px) в styles/mobile.css) — для того, что задаётся не стилями (отступы карты)
import { useEffect, useState } from 'react'

export const PHONE_QUERY = '(max-width: 760px)'
const mq = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(PHONE_QUERY) : null)

export function useIsPhone(): boolean {
  const [on, setOn] = useState(() => !!mq()?.matches)
  useEffect(() => {
    const m = mq()
    if (!m) return
    const upd = () => setOn(m.matches)
    upd()
    m.addEventListener('change', upd)
    return () => m.removeEventListener('change', upd)
  }, [])
  return on
}
