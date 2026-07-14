export const METRIC_THRESHOLD = 75

// Color-tier boundaries for the danger/warning/success visual coding used
// across charts and tables — kept in one place so the two tiers can't drift.
export const COLOR_TIER = { danger: 70, warning: 80 } as const

export const C = {
  danger:  '#e74c3c',
  warning: '#f5a623',
  success: '#00a86b',
  link:    '#007aff',
  grid:    '#ececec',
  muted:   '#5d5d5d',
} as const

export function rateHex(rate: number): string {
  if (rate < COLOR_TIER.danger) return C.danger
  if (rate < COLOR_TIER.warning) return C.warning
  return C.success
}

export function rateTextClass(rate: number): string {
  if (rate < COLOR_TIER.danger) return 'text-danger'
  if (rate < COLOR_TIER.warning) return 'text-warning'
  return 'text-success'
}
