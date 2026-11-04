import { useEffect, useRef, useState } from 'react'

import { createPortal } from 'react-dom'
import {
  ArrowUpRight,
  CalendarDays,
  Check,
  ChevronDown,
  Cpu,
  KeyRound,
  LockKeyhole,
  Plus,
  Settings2,
  Trash2,
  X,
} from 'lucide-react'

import { Badge } from './components/ui/badge'

import './style.css'

import Drawer from './Drawer'

export const formatDuration = (ms) =>
  typeof ms === 'number' && Number.isFinite(ms) && ms >= 0 ? `${(ms / 1000).toFixed(2)} s` : '—'

export const compactCount = (value) => {
  const n = Number(value) || 0
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)} K`
  return String(n)
}

export const initials = (name = '') => name.slice(0, 1).toUpperCase()

export const providerColor = (p) => (p === 'anthropic' ? 'violet' : 'green')

export const localInputTime = (ms) => {
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate())

export const sameDay = (a, b) =>
  a.getFullYear() === b.getFullYear() &&
  a.getMonth() === b.getMonth() &&
  a.getDate() === b.getDate()

export const todayRange = () => {
  const now = new Date()
  return [dayStart(now).getTime(), now.getTime()]
}

export function DateRangePicker({ start, end, onChange }) {
  const anchor = useRef(null)
  const [position, setPosition] = useState({ left: 8, top: 8 })
  const [open, setOpen] = useState(false),
    [month, setMonth] = useState(dayStart(new Date(start)))
  useEffect(() => {
    if (!open) return
    const place = () => {
      const rect = anchor.current.getBoundingClientRect()
      const width = Math.min(792, window.innerWidth - 16)
      setPosition({
        left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
        top: Math.max(8, Math.min(rect.bottom + 7, window.innerHeight - 460)),
      })
    }
    const close = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    window.addEventListener('keydown', close)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('keydown', close)
    }
  }, [open])
  const moveMonth = (n) => setMonth((d) => new Date(d.getFullYear(), d.getMonth() + n, 1))
  const emitRange = (s, e, adjust = 'end') => {
    const now = Date.now(),
      hour = 3600000,
      max = 7 * 86400000
    s = Math.max(0, Math.min(s, now))
    e = Math.max(0, Math.min(e, now))
    if (e - s < hour) {
      if (adjust === 'start') s = Math.max(0, e - hour)
      else e = Math.min(now, s + hour)
      if (e - s < hour) s = Math.max(0, e - hour)
    }
    if (e - s > max) {
      if (adjust === 'start') s = e - max
      else e = s + max
    }
    onChange(s, e)
  }
  const daysFor = (offset) => {
    const first = new Date(month.getFullYear(), month.getMonth() + offset, 1),
      begin = new Date(first)
    begin.setDate(1 - first.getDay())
    return Array.from({ length: 42 }, (_, i) => {
      const d = new Date(begin)
      d.setDate(begin.getDate() + i)
      return d
    })
  }
  const choose = (d) => {
    const ts = dayStart(d).getTime(),
      a = dayStart(new Date(start)).getTime(),
      b = dayStart(new Date(end)).getTime()
    if (sameDay(new Date(a), new Date(b)) || ts < a) {
      emitRange(ts, Math.min(end, ts + 7 * 86400000), 'end')
    } else {
      const time = new Date(end)
      const e = new Date(ts)
      e.setHours(time.getHours(), time.getMinutes(), 0, 0)
      emitRange(start, e.getTime(), 'end')
    }
  }
  const preset = (hours) => {
    const e = Date.now(),
      s = e - hours * 3600000
    emitRange(s, e)
    setMonth(dayStart(new Date(s)))
    setOpen(false)
  }
  const todayPreset = () => {
    const end = Date.now(),
      start = dayStart(new Date(end)).getTime()
    onChange(start, end)
    setMonth(dayStart(new Date(end)))
    setOpen(false)
  }
  const cal = (offset) => {
    const title = new Date(month.getFullYear(), month.getMonth() + offset, 1)
    return (
      <section className="range-calendar" key={offset}>
        <h4>{title.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}</h4>
        <div className="range-week">
          {'SMTWTFS'.split('').map((x, i) => (
            <span key={i}>{x}</span>
          ))}
        </div>
        <div className="range-days">
          {daysFor(offset).map((d, i) => {
            const ts = dayStart(d).getTime(),
              selected =
                ts >= dayStart(new Date(start)).getTime() && ts <= dayStart(new Date(end)).getTime()
            return (
              <button
                key={i}
                disabled={d > Date.now()}
                className={`${d.getMonth() !== title.getMonth() ? 'outside ' : ''}${selected ? 'in-range ' : ''}${sameDay(d, new Date(start)) || sameDay(d, new Date(end)) ? 'range-edge' : ''}`}
                onClick={() => choose(d)}
              >
                {d.getDate()}
              </button>
            )
          })}
        </div>
      </section>
    )
  }
  return (
    <div className="date-range-picker" ref={anchor}>
      <button className="date-range-trigger" onClick={() => setOpen((v) => !v)}>
        <CalendarDays size={15} />
        <span>
          {new Date(start).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}{' '}
          {new Date(start).toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
          })}{' '}
          — {new Date(end).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}{' '}
          {new Date(end).toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
          })}
        </span>
        <ChevronDown size={13} />
      </button>
      {open &&
        createPortal(
          <>
            <button
              className="range-dismiss"
              onClick={() => setOpen(false)}
              aria-label="关闭日期选择"
            />
            <div className="range-popover" style={position} role="dialog" aria-label="选择日期范围">
              <div className="range-calendars">
                <div className="range-month-nav">
                  <button onClick={() => moveMonth(-1)}>‹</button>
                  <button onClick={() => moveMonth(1)}>›</button>
                </div>
                {cal(0)}
                {cal(1)}
                <div className="range-time-row">
                  <label>
                    开始时间
                    <input
                      type="time"
                      value={localInputTime(start).slice(11)}
                      onChange={(e) => {
                        const d = new Date(start),
                          [h, m] = e.target.value.split(':')
                        d.setHours(+h, +m)
                        emitRange(d.getTime(), end, 'start')
                      }}
                    />
                  </label>
                  <label>
                    结束时间
                    <input
                      type="time"
                      value={localInputTime(end).slice(11)}
                      onChange={(e) => {
                        const d = new Date(end),
                          [h, m] = e.target.value.split(':')
                        d.setHours(+h, +m)
                        emitRange(start, d.getTime(), 'end')
                      }}
                    />
                  </label>
                </div>
              </div>
              <aside className="range-presets">
                <button onClick={todayPreset}>当天区间</button>
                {[
                  [1, '最近 1 小时'],
                  [6, '最近 6 小时'],
                  [24, '最近 24 小时'],
                  [168, '最近 7 天'],
                ].map(([h, label]) => (
                  <button key={h} onClick={() => preset(h)}>
                    {label}
                  </button>
                ))}
                <div className="range-zone">
                  范围限制 <b>最短 1 小时 · 最长 7 天</b>
                </div>
              </aside>
            </div>
          </>,
          document.body,
        )}
    </div>
  )
}

export function StatCard({ icon: Icon, label, value, detail, trend, color = 'blue' }) {
  return (
    <div className="stat-card">
      <div className={`stat-icon ${color}`}>
        <Icon size={18} />
      </div>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-detail">
        {trend && (
          <span className="trend">
            <ArrowUpRight size={13} />
            {trend}
          </span>
        )}
        {detail}
      </div>
    </div>
  )
}

export function SettingsDrawer({ title, description, icon: Icon, tone = '', badge, children }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        className="panel settings-drawer-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <span className="settings-drawer-heading">
          {Icon && (
            <span className={`settings-icon ${tone}`}>
              <Icon size={17} />
            </span>
          )}
          <span className="settings-drawer-title">
            <b>{title}</b>
            <small>{description}</small>
          </span>
        </span>
        <span className="settings-drawer-actions">
          {badge}
          <ChevronDown size={16} className="settings-drawer-chevron" aria-hidden="true" />
        </span>
      </button>
      {open && (
        <Drawer title={title} eyebrow="SYSTEM SETTINGS" onClose={() => setOpen(false)}>
          <div className="drawer-content settings-drawer-content">
            <section className="panel settings-drawer-body">{children}</section>
          </div>
        </Drawer>
      )}
    </>
  )
}

export const emptyForm = {
  name: '',
  alias: '',
  provider: 'openai',
  base_url: 'https://api.openai.com',
  upstream_model: '',
  api_keys: [],
  enabled: true,
  thinking_mode: false,
  base_weight: 1,
  auto_weight: 1,
  weight_locked: false,
  proxy_url: '',
  use_system_proxy: true,
  rpm: 0,
  max_input_tokens: 0,
  max_output_tokens: 0,
  timeout_seconds: 90,
  note: '',
}

export function ModelRow({ dep: d, onSelect, onEdit, onDelete, expanded = false }) {
  const health =
    d.health === 'cooldown'
      ? '冷却中'
      : d.health === 'degraded'
        ? '表现波动'
        : d.health === 'no_credentials'
          ? '缺少 Token'
          : d.enabled
            ? '运行正常'
            : '已停用'
  return (
    <tr onClick={onSelect} className={onSelect ? 'clickable-row' : ''}>
      <td>
        <div className="model-cell">
          <div className={`provider-avatar ${providerColor(d.provider)}`}>{initials(d.name)}</div>
          <div>
            <b>{d.name}</b>
            <small>{d.upstream_model}</small>
          </div>
        </div>
      </td>
      <td>
        <code>{d.alias}</code>
      </td>
      {expanded && (
        <td>
          <Badge tone={providerColor(d.provider)}>
            {d.provider === 'anthropic'
              ? 'Anthropic'
              : d.provider === 'responses'
                ? 'Responses'
                : 'OpenAI'}
          </Badge>
        </td>
      )}
      <td>
        <Badge
          tone={
            d.health === 'cooldown'
              ? 'amber'
              : d.health === 'degraded' || d.health === 'no_credentials'
                ? 'red'
                : d.enabled
                  ? 'green'
                  : 'neutral'
          }
          dot
        >
          {health}
        </Badge>
      </td>
      <td>
        <div className="weight-cell">
          <b>{Number(d.effective_weight || 0).toFixed(2)}</b>
          {d.weight_locked ? <LockKeyhole size={12} /> : <span>自动</span>}
        </div>
      </td>
      <td>
        <span className="key-count">
          <KeyRound size={13} />
          {d.key_count}
        </span>
      </td>
      <td>{d.rpm || '—'}</td>
      {expanded && (
        <td>
          {!d.use_system_proxy ? (
            <Badge>直连</Badge>
          ) : d.proxy_url?.trim() ? (
            <Badge tone="blue">自定义</Badge>
          ) : (
            <Badge>系统</Badge>
          )}
        </td>
      )}
      <td>
        <div className="row-actions">
          <button
            title="编辑"
            onClick={(e) => {
              e.stopPropagation()
              onEdit()
            }}
          >
            <Settings2 size={15} />
          </button>
          <button
            title="删除"
            onClick={(e) => {
              e.stopPropagation()
              onDelete()
            }}
          >
            <Trash2 size={15} />
          </button>
        </div>
      </td>
    </tr>
  )
}

export function EventRow({ event: e, fmtTime }) {
  return (
    <div className="event-row">
      <div className={`event-status ${e.status === 'success' ? 'ok' : 'fail'}`}>
        {e.status === 'success' ? <Check size={13} /> : <X size={13} />}
      </div>
      <div className="event-main">
        <b>{e.alias || '未匹配模型'}</b>
        <small>{e.deployment_name || e.error_message || '路由请求'}</small>
      </div>
      <Badge>{e.protocol === 'anthropic' ? 'ANTHROPIC' : 'OPENAI'}</Badge>
      <span className="event-time">
        {formatDuration(e.latency_ms)} <small>{fmtTime(e.created_at)}</small>
      </span>
    </div>
  )
}

export function FormSelect({ value, onChange, options, disabled = false, ariaLabel }) {
  const [open, setOpen] = useState(false)
  const root = useRef(null)
  const trigger = useRef(null)
  const optionRefs = useRef([])
  const menuRef = useRef(null)
  const [menuStyle, setMenuStyle] = useState(null)
  const close = () => {
    setOpen(false)
    trigger.current?.focus()
  }
  useEffect(() => {
    if (!open) return
    const placeMenu = () => {
      const rect = trigger.current?.getBoundingClientRect()
      if (!rect) return
      const dialog = trigger.current.closest('[role="dialog"]')?.getBoundingClientRect()
      const boundaryTop = Math.max(8, dialog?.top ?? 0)
      const boundaryBottom = Math.min(window.innerHeight - 8, dialog?.bottom ?? window.innerHeight)
      const boundaryLeft = Math.max(8, dialog?.left ?? 0)
      const boundaryRight = Math.min(window.innerWidth - 8, dialog?.right ?? window.innerWidth)
      const width = Math.min(rect.width, Math.max(0, boundaryRight - boundaryLeft))
      const below = boundaryBottom - rect.bottom
      const above = rect.top - boundaryTop
      const placeAbove = below < Math.min(220, above) && above > below
      setMenuStyle({
        position: 'fixed',
        left: Math.max(boundaryLeft, Math.min(rect.left, boundaryRight - width)),
        width,
        top: 'auto',
        bottom: 'auto',
        ...(placeAbove
          ? { bottom: window.innerHeight - rect.top + 5, maxHeight: Math.max(0, above - 5) }
          : { top: rect.bottom + 5, maxHeight: Math.max(0, below - 5) }),
      })
    }
    placeMenu()
    const dismiss = (event) => {
      if (!root.current?.contains(event.target) && !menuRef.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    window.addEventListener('resize', placeMenu)
    window.addEventListener('scroll', placeMenu, true)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('resize', placeMenu)
      window.removeEventListener('scroll', placeMenu, true)
    }
  }, [open, options, value])
  const menuReady = menuStyle !== null
  useEffect(() => {
    if (open && menuReady) optionRefs.current[options.findIndex(([v]) => v === value)]?.focus()
  }, [open, menuReady, options, value])
  return (
    <div
      className="form-select"
      ref={root}
      onKeyDownCapture={(event) => {
        if (event.key === 'Escape' && open) {
          event.preventDefault()
          event.stopPropagation()
          close()
        }
      }}
    >
      <button
        type="button"
        ref={trigger}
        className="form-select-trigger"
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault()
            setOpen(true)
          }
        }}
      >
        <span>{options.find(([v]) => v === value)?.[1]}</span>
        <ChevronDown size={13} />
      </button>
      {open && menuStyle && createPortal(
        <div ref={menuRef} className="form-select-options" style={menuStyle} role="listbox">
          {options.map(([v, label], index) => (
            <button
              type="button"
              key={v}
              role="option"
              aria-selected={v === value}
              tabIndex={-1}
              ref={(element) => {
                optionRefs.current[index] = element
              }}
              onKeyDown={(event) => {
                const key = event.key
                if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key)) {
                  event.preventDefault()
                  optionRefs.current[
                    key === 'Home'
                      ? 0
                      : key === 'End'
                        ? options.length - 1
                        : (index + (key === 'ArrowUp' ? -1 : 1) + options.length) % options.length
                  ]?.focus()
                }
              }}
              onClick={() => {
                close()
                if (v !== value) onChange(v)
              }}
            >
              <span>{label}</span>
              {v === value && <Check size={13} />}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  )
}

export function Empty({ title, body, action }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Cpu size={21} />
      </div>
      <b>{title}</b>
      <p>{body}</p>
      {action && (
        <button className="button primary small" onClick={action}>
          <Plus size={14} />
          添加模型
        </button>
      )}
    </div>
  )
}
