import React from 'react'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FormSelect, DateRangePicker, emptyForm } from './shared'
import { useDirtyForm, useRouterData } from './hooks/useRouterData'
import { useToasts } from './hooks/useToasts'
import { useDeploymentEditor } from './hooks/useDeploymentEditor'
import DeploymentDialog from './DeploymentDialog'
import { App } from './main'
import { api, watchManagementEvents } from './lib/managementApi'

vi.mock('./lib/managementApi', () => ({ api: vi.fn(), watchManagementEvents: vi.fn() }))
vi.mock('./AppUpdates', () => ({
  default: () => null,
  AppUpdatesProvider: ({ children }) => children,
}))
vi.mock('./DesktopStartup', () => ({ default: ({ children }) => children }))
const stats = {
  total: 600,
  failed: 20,
  tokens: 8000,
  tokenEvents: 600,
  rate: 96.67,
  avg: 100,
  p95: 120,
  buckets: Array.from({ length: 10 }, () => ({ success: 58, error: 2 })),
}
function respond(path) {
  if (path.startsWith('/events/page')) return { items: [], has_more: false, next_cursor: null }
  if (path.startsWith('/events/stats')) return stats
  if (path.startsWith('/overview')) return { providers: [], models: [], stats }
  if (path === '/bootstrap')
    return { base_url: 'http://127.0.0.1:8787/v1', client_key: 'local', data_dir: 'test' }
  if (path === '/settings')
    return {
      ai: { enabled: false, model: '' },
      rules: { enabled: true, failure_threshold: 3 },
      provider_presets: {},
    }
  if (path === '/summary') return { deployments: { total: 0, enabled: 0 }, cooldowns: 0 }
  if (path === '/settings/retention') return { retention_days: 0 }
  if (path === '/network') return { port: 8787 }
  if (path === '/network/recommendations') return { ports: [] }
  return []
}
beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  api.mockImplementation(async (path) => respond(path))
})
afterEach(() => vi.useRealTimers())

it('FormSelect supports up/down, selection, Escape and focus restoration', async () => {
  const change = vi.fn()
  const user = userEvent.setup()
  render(
    <FormSelect
      value="b"
      options={[
        ['a', '甲'],
        ['b', '乙'],
        ['c', '丙'],
      ]}
      onChange={change}
    />,
  )
  const trigger = screen.getByRole('button', { name: '乙' })
  await user.click(trigger)
  expect(screen.getByRole('option', { name: '乙' })).toHaveFocus()
  await user.keyboard('{ArrowUp}')
  expect(screen.getByRole('option', { name: '甲' })).toHaveFocus()
  await user.keyboard('{Enter}')
  expect(change).toHaveBeenCalledWith('a')
  expect(trigger).toHaveFocus()
  await user.click(trigger)
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
})

it('date range picker opens and closes with Escape', async () => {
  const user = userEvent.setup()
  const now = Date.now()
  render(<DateRangePicker start={now - 3600000} end={now} onChange={vi.fn()} />)
  await user.click(screen.getAllByRole('button')[0])
  expect(screen.getByRole('dialog', { name: '选择日期范围' })).toBeInTheDocument()
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog', { name: '选择日期范围' })).not.toBeInTheDocument()
})

it('settings synchronize until edited and synchronize again after save', () => {
  const { result, rerender } = renderHook(({ remote }) => useDirtyForm(remote, { model: '' }), {
    initialProps: { remote: { model: 'first' } },
  })
  expect(result.current[0].model).toBe('first')
  act(() => result.current[1]({ model: 'editing' }))
  rerender({ remote: { model: 'external' } })
  expect(result.current[0].model).toBe('editing')
  act(() => result.current[2]({ model: 'saved' }))
  rerender({ remote: { model: 'new external' } })
  expect(result.current[0].model).toBe('new external')
})

it('toast types remain explicit and queued messages expire in order', () => {
  vi.useFakeTimers()
  const { result } = renderHook(useToasts)
  act(() => {
    result.current[1]('first', 'error')
    result.current[1]('second', 'warn')
  })
  expect(result.current[0]).toMatchObject({ message: 'first', type: 'error' })
  act(() => vi.advanceTimersByTime(3200))
  expect(result.current[0]).toMatchObject({ message: 'second', type: 'warn' })
  act(() => vi.advanceTimersByTime(3200))
  expect(result.current[0]).toBeUndefined()
})

it('deployment editing leaves saved credentials untouched and trims fields', async () => {
  const { result } = renderHook(() =>
    useDeploymentEditor({ refresh: vi.fn(), setDeps: vi.fn(), setToast: vi.fn() }),
  )
  act(() =>
    result.current.openEdit({
      ...emptyForm,
      id: 'dep',
      base_url: ' https://example.test/ ',
      upstream_model: ' model ',
      name: '',
      alias: '',
    }),
  )
  await act(() => result.current.saveDeployment({ preventDefault() {} }))
  const [path, options] = api.mock.calls.find(([path]) => path === '/deployments/dep')
  expect(path).toBe('/deployments/dep')
  expect(JSON.parse(options.body)).toMatchObject({
    api_keys: [],
    base_url: 'https://example.test',
    name: 'model',
    alias: 'model',
  })
})

it('missing connection inputs produce a warning before making requests', async () => {
  const toast = vi.fn()
  const { result } = renderHook(() =>
    useDeploymentEditor({ refresh: vi.fn(), setDeps: vi.fn(), setToast: toast }),
  )
  await act(() => result.current.testDeployment())
  expect(api).not.toHaveBeenCalled()
  expect(toast).toHaveBeenCalledWith(expect.any(String), 'warn')
})

it('deployment dialog exposes required inputs and closes with Escape', async () => {
  const close = vi.fn()
  const user = userEvent.setup()
  render(
    <DeploymentDialog
      editing={{ id: 'dep' }}
      form={emptyForm}
      setForm={vi.fn()}
      emptyForm={emptyForm}
      keyText=""
      setKeyText={vi.fn()}
      availableModels={[]}
      setAvailableModels={vi.fn()}
      deps={[]}
      onSave={vi.fn()}
      onClose={close}
    />,
  )
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(document.querySelectorAll('input[required]').length).toBeGreaterThan(0)
  await user.keyboard('{Escape}')
  expect(close).toHaveBeenCalledOnce()
})

it('page data does not load the legacy event sample for overview', async () => {
  const { result } = renderHook(() =>
    useRouterData({ tab: 'overview', start: 0, end: 1000, q: '', page: 1, live: false }, vi.fn()),
  )
  await waitFor(() => expect(result.current.overview?.stats.total).toBe(600))
  expect(api.mock.calls.some(([path]) => path.startsWith('/events'))).toBe(false)
})

it('old filter responses cannot overwrite new results', async () => {
  let resolveOld
  const old = new Promise((resolve) => {
    resolveOld = resolve
  })
  api.mockImplementation(async (path) =>
    path.startsWith('/overview?') && path.includes('end=1000') ? old : respond(path),
  )
  const { result, rerender } = renderHook(
    ({ end }) =>
      useRouterData({ tab: 'overview', start: 0, end, q: '', page: 1, live: false }, vi.fn()),
    { initialProps: { end: 1000 } },
  )
  rerender({ end: 2000 })
  await waitFor(() => expect(result.current.overview?.stats.total).toBe(600))
  await act(async () => resolveOld({ stats: { total: 1 }, providers: [], models: [] }))
  expect(result.current.overview.stats.total).toBe(600)
})

it('live refresh keeps historical list and statistics inside the selected range', async () => {
  const now = Date.now()
  const { result, rerender } = renderHook(
    ({ start, end }) =>
      useRouterData({ tab: 'logs', start, end, q: '', page: 1, live: true }, vi.fn()),
    { initialProps: { start: now - 3600000, end: now } },
  )
  await waitFor(() => expect(result.current.stats).not.toBeNull())
  expect(watchManagementEvents).toHaveBeenCalledOnce()
  const signal = watchManagementEvents.mock.calls[0][2]
  api.mockClear()
  watchManagementEvents.mockClear()
  const start = now - 2 * 86400000
  const end = now - 86400000
  rerender({ start, end })
  await waitFor(() => expect(result.current.loading).toBe(false))
  expect(signal.aborted).toBe(true)
  await act(() => result.current.refresh())
  const queries = api.mock.calls
    .filter(([path]) => /^\/events\/(page|stats)\?/.test(path))
    .map(([path]) => new URLSearchParams(path.split('?')[1]))
  expect(queries).toHaveLength(4)
  for (const query of queries) {
    expect(query.get('start')).toBe(String(start))
    expect(query.get('end')).toBe(String(end))
  }
  expect(watchManagementEvents).not.toHaveBeenCalled()
})

it('a current live window keeps rolling after one minute', async () => {
  vi.useFakeTimers()
  const now = Date.now()
  const { result } = renderHook(() =>
    useRouterData(
      { tab: 'logs', start: now - 3600000, end: now, q: '', page: 1, live: true },
      vi.fn(),
    ),
  )
  await act(async () => {})
  api.mockClear()
  await act(() => vi.advanceTimersByTimeAsync(120000))
  await act(() => result.current.refresh())
  const calls = api.mock.calls.filter(([path]) => /^\/events\/(page|stats)\?/.test(path))
  for (const [path] of calls.slice(-2)) {
    expect(new URLSearchParams(path.split('?')[1]).get('end')).toBe(String(now + 120000))
  }
  expect(calls.length).toBeGreaterThan(0)
})

it('hidden documents stop polling and visible documents refresh once', async () => {
  const { result } = renderHook(() =>
    useRouterData({ tab: 'overview', start: 0, end: 1000, q: '', page: 1, live: false }, vi.fn()),
  )
  await waitFor(() => expect(result.current.overview).not.toBeNull())
  vi.useFakeTimers()
  api.mockClear()
  Object.defineProperty(document, 'hidden', { configurable: true, value: true })
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  await act(() => vi.advanceTimersByTimeAsync(36000))
  expect(api).not.toHaveBeenCalled()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  await act(() => document.dispatchEvent(new Event('visibilitychange')))
  expect(api.mock.calls.filter(([path]) => path.startsWith('/overview'))).toHaveLength(1)
})

it('all five pages render after navigation with the new data interfaces', async () => {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByRole('heading', { name: '服务概览' })
  await user.click(screen.getByTitle('模型路由'))
  expect(await screen.findByRole('heading', { name: '模型路由' })).toBeInTheDocument()
  await user.click(screen.getByTitle('请求日志'))
  expect(await screen.findByRole('heading', { name: '请求日志' })).toBeInTheDocument()
  await user.click(screen.getByTitle('自动调优'))
  expect(await screen.findByRole('heading', { name: '自动调优' })).toBeInTheDocument()
  api.mockImplementation(async (path) =>
    path === '/ai/analyze'
      ? { status: 'failed', report: { analysis_error: 'test failure' } }
      : respond(path),
  )
  await user.click(screen.getByRole('button', { name: '生成综合分析' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('AI 分析失败')
  await user.click(screen.getByTitle('系统设置'))
  expect(await screen.findByRole('heading', { name: '系统设置' })).toBeInTheDocument()
  await user.click(screen.getByText('数据与隐私'))
  expect(await screen.findByRole('button', { name: '历史记录保留' })).toHaveTextContent(
    '关闭自动清理',
  )
})

it('an unsaved tuning form survives navigation and remote settings refreshes', async () => {
  const user = userEvent.setup()
  render(<App />)
  await screen.findByRole('heading', { name: '服务概览' })
  await user.click(screen.getByTitle('自动调优'))
  await user.click(screen.getByTitle('调优配置'))
  await user.type(screen.getByLabelText('分析模型'), 'draft-model')
  await user.keyboard('{Escape}')
  await user.click(screen.getByTitle('系统设置'))
  await screen.findByRole('heading', { name: '系统设置' })
  await user.click(screen.getByTitle('自动调优'))
  await user.click(screen.getByTitle('调优配置'))
  expect(screen.getByLabelText('分析模型')).toHaveValue('draft-model')
})
