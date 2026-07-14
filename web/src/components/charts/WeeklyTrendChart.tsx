import type { Key } from 'react'
import {
  Area, AreaChart, CartesianGrid, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { WeeklyStat } from '../../types'
import { C, METRIC_THRESHOLD } from '../../lib/constants'

interface Props {
  data:          WeeklyStat[]
  loading:       boolean
  selectedWeek?: string
  onSelect?:     (week: string) => void
}

interface DotProps {
  cx?:     number
  cy?:     number
  key?:    Key | null
  payload?: WeeklyStat
}

function fmtWeekLabel(label: string): string {
  const start = label.split('/')[0]
  if (!start) return label
  const d = new Date(start)
  if (isNaN(d.getTime())) return label
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function WeeklyTrendChart({ data, loading, selectedWeek, onSelect }: Props) {
  if (loading) return <div className="h-48 bg-arctic-mist rounded animate-pulse" />
  if (!data.length) return <p className="text-xs text-pewter py-4">No data available.</p>

  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
        <defs>
          <linearGradient id="trendGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor={C.link} stopOpacity={0.18} />
            <stop offset="95%" stopColor={C.link} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis
          dataKey="week"
          tickFormatter={fmtWeekLabel}
          tick={{ fontSize: 11, fill: C.muted }}
          interval="preserveStartEnd"
        />
        <YAxis
          domain={[0, 100]}
          tickFormatter={(v) => `${v}%`}
          tick={{ fontSize: 11, fill: C.muted }}
          width={40}
        />
        <ReferenceLine y={METRIC_THRESHOLD} stroke={C.danger} strokeDasharray="3 3" />
        <Tooltip
          formatter={(v) => [`${typeof v === 'number' ? v : ''}%`, 'Rate']}
          labelFormatter={(label) => fmtWeekLabel(String(label ?? ''))}
          contentStyle={{ fontSize: 12, borderRadius: 10, border: `1px solid ${C.grid}` }}
        />
        <Area
          type="monotone"
          dataKey="metric_rate"
          stroke={C.link}
          strokeWidth={2}
          fill="url(#trendGrad)"
          dot={(props: DotProps) => {
            const isSelected = selectedWeek && props.payload?.week === selectedWeek
            return (
              <circle
                key={props.key}
                cx={props.cx}
                cy={props.cy}
                r={isSelected ? 5 : 3}
                fill={isSelected ? C.link : (selectedWeek ? '#b0c8ef' : C.link)}
                strokeWidth={0}
                cursor={onSelect ? 'pointer' : 'default'}
                onClick={() => onSelect?.(props.payload?.week ?? '')}
              />
            )
          }}
          activeDot={{ r: 5, cursor: onSelect ? 'pointer' : 'default',
            onClick: (_: unknown, payload: any) =>
              onSelect?.(payload?.activePayload?.[0]?.payload?.week ?? '') }}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
