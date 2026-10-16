// Липкие колонки (.sticky в go.css) встают под шапку по её настоящей высоте: шапка растёт, когда в ней баннер ошибки связи
import { useEffect } from 'react'

/** Пока страница открыта, держит высоту шапки .yhead в CSS-переменной --yhead; без неё .sticky считает шапку в 113px */
export function useHeadHeight() {
  useEffect(() => {
    const head = document.querySelector<HTMLElement>('.yhead')
    if (!head || typeof ResizeObserver === 'undefined') return
    const root = document.documentElement.style
    const ro = new ResizeObserver(() => root.setProperty('--yhead', `${head.offsetHeight}px`))
    ro.observe(head)
    return () => { ro.disconnect(); root.removeProperty('--yhead') }
  }, [])
}
