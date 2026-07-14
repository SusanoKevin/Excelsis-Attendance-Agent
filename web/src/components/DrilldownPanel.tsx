import { useState } from 'react'
import WeeklyTrendChart from './charts/WeeklyTrendChart'
import { AlertItem, DrillLevel, WeeklyStat } from '../types'
import { ChartSelection } from '../hooks/useChartSelection'
import { C, COLOR_TIER, METRIC_THRESHOLD, rateTextClass } from '../lib/constants'

type SortCol = 'positive_count' | 'metric_rate'
type SortDir = 'asc' | 'desc'

function rowStyle(rate: number): string {
  const base = 'border-b border-arctic-mist transition-colors'
  if (rate < COLOR_TIER.danger) return `${base} border-l-4 border-l-danger bg-red-50 hover:bg-red-100/60`
  return `${base} border-l-4 border-l-warning hover:bg-arctic-mist/50`
}

function Sparkline({ points, rate }: { points: (number | null)[], rate: number }) {
  const W = 56, H = 20
  const valid = points.filter((p): p is number => p !== null)
  if (valid.length < 2) return <span className="w-14 inline-block" />

  const segs: string[] = []
  let open = false
  points.forEach((p, i) => {
    if (p === null) { open = false; return }
    const x = (i / (points.length - 1)) * W
    const y = H - (p / 100) * H
    segs.push(`${open ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`)
    open = true
  })

  return (
    <svg width={W} height={H} className="overflow-visible" aria-hidden="true">
      <path d={segs.join(' ')} fill="none"
        stroke={rate < COLOR_TIER.danger ? C.danger : C.warning}
        strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function SortTh({ col, label, active, dir, onSort }: {
  col: SortCol; label: string; active: boolean; dir: SortDir; onSort: (col: SortCol) => void
}) {
  return (
    <th className="px-5 py-3 text-right text-xs text-pewter uppercase tracking-widest cursor-pointer select-none hover:text-carbon transition-colors"
      onClick={() => onSort(col)}>
      {label}
      <span className="ml-1 opacity-50">{active ? (dir === 'asc' ? '↑' : '↓') : '↕'}</span>
    </th>
  )
}

interface Props {
  drillLevel:   DrillLevel
  drillClass:   string | null
  drillStudent: number | null
  atRisk:       AlertItem[]
  sparklines:   Record<string, (number | null)[]>
  loading:      boolean
  onInspect:    (entityId: number) => void
  selection?:   ChartSelection
}

export default function DrilldownPanel({
  drillLevel, drillClass, drillStudent, atRisk, sparklines, loading, onInspect, selection,
}: Props) {
  const [sortCol, setSortCol] = useState<SortCol>('metric_rate')
  const [sortDir, setSortDir] = useState<SortDir>('asc')

  function toggleSort(col: SortCol) {
    if (col === sortCol) setSortDir((d) => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('asc') }
  }

  const showTable = drillLevel === 'group' ||
    (drillLevel === 'overview' && !!(selection?.group || selection?.threshold))

  if (showTable) {
    const effectiveGroup = drillLevel === 'group' ? drillClass : (selection?.group ?? null)
    const entities = atRisk.filter((s) => {
      const groupMatch = !effectiveGroup || s.group_name === effectiveGroup
      const thresholdMatch = !selection?.threshold ||
        (selection.threshold === 'above' ? s.metric_rate >= METRIC_THRESHOLD : s.metric_rate < METRIC_THRESHOLD)
      return groupMatch && thresholdMatch
    })

    const sorted = [...entities].sort((a, b) => {
      const mult = sortDir === 'asc' ? 1 : -1
      return (a[sortCol] - b[sortCol]) * mult
    })

    return (
      <div className="bg-fog border border-arctic-mist rounded-[10px] overflow-hidden mb-8">
        <div className="px-5 py-4 border-b border-arctic-mist">
          <h3 className="text-sm font-semibold text-carbon">
            Threshold Alerts{effectiveGroup ? ` — ${effectiveGroup}` : ''}
          </h3>
          <p className="text-xs text-pewter mt-0.5">Below {METRIC_THRESHOLD}% metric threshold</p>
        </div>

        {sorted.length === 0 ? (
          <p className="text-xs text-pewter px-5 py-4">No threshold alerts in this group.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Threshold alerts{effectiveGroup ? ` for ${effectiveGroup}` : ''}</caption>
              <thead>
                <tr className="border-b border-arctic-mist">
                  <th className="px-5 py-3 text-left text-xs text-pewter uppercase tracking-widest">Entity</th>
                  <SortTh col="positive_count" label="Above"  active={sortCol === 'positive_count'} dir={sortDir} onSort={toggleSort} />
                  <SortTh col="metric_rate"    label="Rate"   active={sortCol === 'metric_rate'}    dir={sortDir} onSort={toggleSort} />
                  <th className="px-5 py-3 text-left text-xs text-pewter uppercase tracking-widest">6-week trend</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {sorted.map((s) => {
                  const spark = sparklines[String(s.entity_id)]
                  const highlighted = selection?.group && s.group_name === selection.group
                  return (
                    <tr key={s.entity_id} className={`${rowStyle(s.metric_rate)}${highlighted ? ' ring-1 ring-inset ring-link-blue/30' : ''}`}>
                      <td className="px-5 py-3 text-carbon">{s.label ?? `#${s.entity_id}`}</td>
                      <td className="px-5 py-3 text-right text-pewter font-mono text-xs">{s.positive_count}</td>
                      <td className={`px-5 py-3 text-right font-mono text-xs font-medium ${rateTextClass(s.metric_rate)}`}>
                        {s.metric_rate}%
                      </td>
                      <td className="px-5 py-3">
                        {spark && <Sparkline points={spark} rate={s.metric_rate} />}
                      </td>
                      <td className="px-5 py-3">
                        <button onClick={() => onInspect(s.entity_id)}
                          className="text-xs text-link-blue hover:underline rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-link-blue">
                          Inspect
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  if (drillLevel !== 'entity' || drillStudent === null) return null

  const entity    = atRisk.find((s) => s.entity_id === drillStudent)
  const sparkRaw  = sparklines[String(drillStudent)] ?? []
  const weeklyData: WeeklyStat[] = sparkRaw.map((rate, i) => ({
    week:           `Week ${i + 1}`,
    total:          0,
    positive_count: 0,
    metric_rate:    rate ?? 0,
  }))

  return (
    <div className="bg-fog border border-arctic-mist rounded-[10px] overflow-hidden mb-8">
      <div className="px-5 py-4 border-b border-arctic-mist">
        <h3 className="text-sm font-semibold text-carbon">
          {entity?.label ?? `Entity #${drillStudent}`}
        </h3>
        {entity && (
          <p className="text-xs text-pewter mt-0.5">
            {entity.group_name} · {entity.metric_rate}% metric rate
          </p>
        )}
      </div>
      <div className="p-5">
        <WeeklyTrendChart data={weeklyData} loading={loading} />
      </div>
    </div>
  )
}
