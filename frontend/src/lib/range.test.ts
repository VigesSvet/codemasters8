import { describe, expect, it } from 'vitest'
import {
  bandGradient, boundProblem, clamp, commitBound, isFull, keyMove, keyValue, moveHandle, nearestHandle, parseBound, pctToValue, pointerToValue,
  rangeText, snap, valueToPct, type Bounds,
} from './range'

const B: Bounds = { min: 0, max: 100 }

describe('значение и позиция', () => {
  it('clamp: в границах, NaN — нижняя граница', () => {
    expect(clamp(-5, 0, 100)).toBe(0)
    expect(clamp(105, 0, 100)).toBe(100)
    expect(clamp(42, 0, 100)).toBe(42)
    expect(clamp(Number.NaN, 0, 100)).toBe(0)
  })
  it('snap: к целому и к шагу, без -0', () => {
    expect(snap(24.5)).toBe(25)
    expect(snap(24.49)).toBe(24)
    expect(snap(13, 5)).toBe(15)
    expect(snap(12, 5)).toBe(10)
    expect(Object.is(snap(-0.2), 0)).toBe(true)
    expect(snap(7.4, 0)).toBe(7)
  })
  it('valueToPct и pctToValue — взаимно обратны на целых', () => {
    expect(valueToPct(25, 0, 100)).toBe(25)
    expect(valueToPct(15, 10, 20)).toBe(50)
    expect(valueToPct(150, 0, 100)).toBe(100)
    expect(valueToPct(5, 5, 5)).toBe(0)
    for (let v = 0; v <= 100; v += 1) expect(pctToValue(valueToPct(v, 0, 100), B)).toBe(v)
    expect(pctToValue(33.3, B)).toBe(33)
    expect(pctToValue(-10, B)).toBe(0)
    expect(pctToValue(120, B)).toBe(100)
    expect(pctToValue(50, { min: 0, max: 10, step: 2 })).toBe(6)
  })
  it('pointerToValue: координата указателя на полосе → целое значение', () => {
    // полоса 200 px от x=100
    expect(pointerToValue(100, 100, 200, B)).toBe(0)
    expect(pointerToValue(150, 100, 200, B)).toBe(25)
    expect(pointerToValue(300, 100, 200, B)).toBe(100)
    expect(pointerToValue(151, 100, 200, B)).toBe(26)
    expect(pointerToValue(50, 100, 200, B)).toBe(0)
    expect(pointerToValue(400, 100, 200, B)).toBe(100)
    expect(pointerToValue(150, 100, 0, B)).toBe(0)
  })
})

describe('ручки', () => {
  it('moveHandle: «от» не дальше «до», «до» не ближе «от», в границах', () => {
    expect(moveHandle([25, 60], 0, 70, B)).toEqual([60, 60])
    expect(moveHandle([25, 60], 0, -3, B)).toEqual([0, 60])
    expect(moveHandle([25, 60], 0, 30.6, B)).toEqual([31, 60])
    expect(moveHandle([25, 60], 1, 10, B)).toEqual([25, 25])
    expect(moveHandle([25, 60], 1, 130, B)).toEqual([25, 100])
    expect(moveHandle([25, 60], 1, 80, B)).toEqual([25, 80])
  })
  it('keyValue: стрелки ±1, PageUp/PageDown ±10, Home/End — края; чужие клавиши — null', () => {
    expect(keyValue('ArrowRight', 25, B)).toBe(26)
    expect(keyValue('ArrowUp', 25, B)).toBe(26)
    expect(keyValue('ArrowLeft', 25, B)).toBe(24)
    expect(keyValue('ArrowDown', 25, B)).toBe(24)
    expect(keyValue('PageUp', 25, B)).toBe(35)
    expect(keyValue('PageDown', 25, B)).toBe(15)
    expect(keyValue('Home', 25, B)).toBe(0)
    expect(keyValue('End', 25, B)).toBe(100)
    expect(keyValue('Tab', 25, B)).toBeNull()
    expect(keyValue('a', 25, B)).toBeNull()
    expect(keyValue('PageUp', 25, { ...B, step: 5 })).toBe(75)
    expect(keyValue('PageUp', 25, { ...B, page: 20 })).toBe(45)
  })
  it('keyMove: клавиша не уводит за край и за соседнюю ручку', () => {
    expect(keyMove('ArrowLeft', [0, 60], 0, B)).toEqual([0, 60])
    expect(keyMove('ArrowRight', [25, 100], 1, B)).toEqual([25, 100])
    expect(keyMove('PageUp', [55, 60], 0, B)).toEqual([60, 60])
    expect(keyMove('PageDown', [25, 30], 1, B)).toEqual([25, 25])
    expect(keyMove('End', [25, 60], 0, B)).toEqual([60, 60])
    expect(keyMove('Home', [25, 60], 1, B)).toEqual([25, 25])
    expect(keyMove('Home', [25, 60], 0, B)).toEqual([0, 60])
    expect(keyMove('End', [25, 60], 1, B)).toEqual([25, 100])
    expect(keyMove('Enter', [25, 60], 1, B)).toBeNull()
  })
  it('nearestHandle: ближайшая ручка; ручки в одной точке — по сторону нажатия', () => {
    expect(nearestHandle([25, 60], 10)).toBe(0)
    expect(nearestHandle([25, 60], 40)).toBe(0)
    expect(nearestHandle([25, 60], 43)).toBe(1)
    expect(nearestHandle([25, 60], 90)).toBe(1)
    expect(nearestHandle([50, 50], 49)).toBe(0)
    expect(nearestHandle([50, 50], 51)).toBe(1)
    expect(nearestHandle([100, 100], 100)).toBe(1)
  })
})

describe('поля «от» / «до»', () => {
  it('parseBound: только целые', () => {
    expect(parseBound('25')).toBe(25)
    expect(parseBound(' 60 ')).toBe(60)
    expect(parseBound('+7')).toBe(7)
    expect(parseBound('-3')).toBe(-3)
    expect(parseBound('007')).toBe(7)
    expect(parseBound('')).toBeNull()
    expect(parseBound('2.5')).toBeNull()
    expect(parseBound('25,5')).toBeNull()
    expect(parseBound('abc')).toBeNull()
    expect(parseBound('1e2')).toBeNull()
  })
  it('boundProblem: число, границы, порядок «от» ≤ «до»', () => {
    expect(boundProblem('25', [0, 100], 0, B)).toBe('')
    expect(boundProblem('', [0, 100], 0, B)).toBe('Введите целое число от 0 до 100')
    expect(boundProblem('2.5', [0, 100], 1, B)).toBe('Введите целое число от 0 до 100')
    expect(boundProblem('120', [0, 100], 1, B)).toBe('Допустимо от 0 до 100')
    expect(boundProblem('-1', [0, 100], 0, B)).toBe('Допустимо от 0 до 100')
    expect(boundProblem('70', [25, 60], 0, B)).toBe('«от» — не больше 60')
    expect(boundProblem('10', [25, 60], 1, B)).toBe('«до» — не меньше 25')
    expect(boundProblem('60', [25, 60], 0, B)).toBe('')
    expect(boundProblem('25', [25, 60], 1, B)).toBe('')
  })
  it('commitBound: ближайшее допустимое; не число — прежний диапазон', () => {
    expect(commitBound('30', [25, 60], 0, B)).toEqual([30, 60])
    expect(commitBound('70', [25, 60], 0, B)).toEqual([60, 60])
    expect(commitBound('500', [25, 60], 1, B)).toEqual([25, 100])
    expect(commitBound('-5', [25, 60], 0, B)).toEqual([0, 60])
    expect(commitBound('10', [25, 60], 1, B)).toEqual([25, 25])
    expect(commitBound('', [25, 60], 1, B)).toEqual([25, 60])
    expect(commitBound('x', [25, 60], 0, B)).toEqual([25, 60])
  })
  it('isFull и rangeText', () => {
    expect(isFull([0, 100], B)).toBe(true)
    expect(isFull([1, 100], B)).toBe(false)
    expect(isFull([0, 99], B)).toBe(false)
    expect(rangeText([25, 100])).toBe('25–100')
  })
})

describe('градиент полосы', () => {
  const bands = [
    { from: 0, to: 25, color: 'green' },
    { from: 25, to: 60, color: 'yellow' },
    { from: 60, to: 100, color: 'red' },
  ]
  it('без перехода — резкие границы на порогах', () => {
    expect(bandGradient(bands, 0, 100)).toBe('linear-gradient(90deg, green 0%, green 25%, yellow 25%, yellow 60%, red 60%, red 100%)')
  })
  it('мягкий переход — поровну по обе стороны порога, края полосы — в цвет крайних зон', () => {
    expect(bandGradient(bands, 0, 100, 8)).toBe('linear-gradient(90deg, green 0%, green 21%, yellow 29%, yellow 56%, red 64%, red 100%)')
  })
  it('шкала не от 0 до 100 и пустой список', () => {
    expect(bandGradient([{ from: 0, to: 5, color: 'a' }, { from: 5, to: 10, color: 'b' }], 0, 10)).toBe('linear-gradient(90deg, a 0%, a 50%, b 50%, b 100%)')
    expect(bandGradient([], 0, 100)).toBe('transparent')
  })
  it('переход шире зоны не ломает порядок остановок', () => {
    const g = bandGradient([{ from: 0, to: 2, color: 'a' }, { from: 2, to: 4, color: 'b' }, { from: 4, to: 100, color: 'c' }], 0, 100, 10)
    const at = [...g.matchAll(/([\d.]+)%/g)].map((m) => Number(m[1]))
    expect(at).toEqual([...at].sort((x, y) => x - y))
  })
})
