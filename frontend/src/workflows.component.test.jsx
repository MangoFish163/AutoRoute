import { useState } from 'react'
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from './lib/managementApi'
import { useTuning } from './hooks/useTuning'
import { useHistory } from './hooks/useHistory'
import { useLogFilters } from './hooks/useLogFilters'
import { useDeploymentActions } from './hooks/useDeploymentActions'

vi.mock('./lib/managementApi', () => ({ api: vi.fn(), watchManagementEvents: vi.fn() }))

const initial = {
  ai: { enabled: true, model: 'analysis', api_key: '', key_saved: true },
  rules: { enabled: true, failure_threshold: 3 },
  provider_presets: { version: 'initial' },
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
function useTuningWithSettings(options) {
  const [settings, setSettings] = useState(initial)
  return { ...useTuning({ settings, setSettings, ...options }), settings, setSettings }
}
beforeEach(() => vi.resetAllMocks())
afterEach(() => vi.useRealTimers())

it('AI saves clear the entered secret and release busy state', async () => {
  const refresh = vi.fn()
  const setToast = vi.fn()
  const { result } = renderHook(() => useTuningWithSettings({ refresh, setToast, setTab: vi.fn() }))
  act(() => result.current.setAiForm((old) => ({ ...old, api_key: 'new-secret' })))
  api.mockResolvedValue({ ...initial.ai, api_key: '', key_saved: true })
  await act(() => result.current.saveAi({ preventDefault() {} }))
  expect(JSON.parse(api.mock.calls[0][1].body).api_key).toBe('new-secret')
  expect(result.current.aiForm.api_key).toBe('')
  expect(result.current.busy).toBe(false)
  expect(refresh).toHaveBeenCalledOnce()
  expect(setToast).toHaveBeenCalledWith('AI 分析配置已保存')
})

it('failed AI and rule toggles restore the previous enabled state', async () => {
  const setToast = vi.fn()
  const { result } = renderHook(() =>
    useTuningWithSettings({ refresh: vi.fn(), setToast, setTab: vi.fn() }),
  )
  api.mockRejectedValue(new Error('save failed'))
  await act(() => result.current.toggleAi(false))
  await act(() => result.current.toggleRules(false))
  expect(result.current.aiForm.enabled).toBe(true)
  expect(result.current.ruleForm.enabled).toBe(true)
  expect(result.current.busy).toBe(false)
  expect(setToast).toHaveBeenCalledWith('save failed', 'error')
})

it('saving rules preserves settings updated while the request was pending', async () => {
  const pending = deferred()
  api.mockReturnValue(pending.promise)
  const { result } = renderHook(() =>
    useTuningWithSettings({ refresh: vi.fn(), setToast: vi.fn(), setTab: vi.fn() }),
  )
  let saving
  act(() => {
    saving = result.current.saveRules({ preventDefault() {} })
  })
  act(() =>
    result.current.setSettings((old) => ({
      ...old,
      provider_presets: { version: 'new' },
    })),
  )
  await act(async () => {
    pending.resolve({ enabled: true, failure_threshold: 5 })
    await saving
  })
  expect(result.current.settings.provider_presets.version).toBe('new')
  expect(result.current.ruleForm.failure_threshold).toBe(5)
})

it('switching history cancels old work and ignores late failures', async () => {
  const old = deferred(),
    next = deferred()
  api.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise)
  const setToast = vi.fn()
  const { result } = renderHook(() => useHistory({ settings: initial, setToast }))
  let first, second
  act(() => {
    first = result.current.openHistory('weights')
  })
  act(() => {
    second = result.current.openHistory('audits')
  })
  expect(api.mock.calls[0][1].signal.aborted).toBe(true)
  await act(async () => {
    old.reject(new Error('old error'))
    await first
  })
  expect(result.current.historyLoading).toBe(true)
  expect(setToast).not.toHaveBeenCalled()
  await act(async () => {
    next.resolve({ items: [{ id: 'audit' }], total: 1 })
    await second
  })
  expect(result.current.historyDrawer).toBe('audits')
  expect(result.current.historyData.audits.items).toEqual([{ id: 'audit' }])
  expect(result.current.historyLoading).toBe(false)
})

it('late results from an older opening cannot overwrite the same history kind', async () => {
  const old = deferred()
  api.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ items: [{ id: 'new' }], total: 1 })
  const { result } = renderHook(() => useHistory({ settings: initial, setToast: vi.fn() }))
  let first
  act(() => {
    first = result.current.openHistory('weights')
  })
  await act(() => result.current.openHistory('weights'))
  await act(async () => {
    old.resolve({ items: [{ id: 'old' }], total: 1 })
    await first
  })
  expect(result.current.historyData.weights.items).toEqual([{ id: 'new' }])
})

it('history pagination appends once even when load more is clicked twice', async () => {
  const page = deferred()
  api
    .mockResolvedValueOnce({ items: [{ id: 'first' }], total: 2 })
    .mockReturnValueOnce(page.promise)
  const { result } = renderHook(() => useHistory({ settings: initial, setToast: vi.fn() }))
  await act(() => result.current.openHistory('weights'))
  let loading
  act(() => {
    loading = result.current.loadMoreHistory()
    result.current.loadMoreHistory()
  })
  expect(api).toHaveBeenCalledTimes(2)
  expect(api.mock.calls[1][0]).toBe('/history/weights?limit=20&offset=1')
  await act(async () => {
    page.resolve({ items: [{ id: 'second' }], total: 2 })
    await loading
  })
  expect(result.current.historyData.weights.items.map((item) => item.id)).toEqual([
    'first',
    'second',
  ])
})

it('closing history cancels the request and keeps late results out of the drawer', async () => {
  const pending = deferred()
  api.mockReturnValue(pending.promise)
  const { result } = renderHook(() => useHistory({ settings: initial, setToast: vi.fn() }))
  let loading
  act(() => {
    loading = result.current.openHistory('weights')
  })
  act(() => result.current.setHistoryDrawer(null))
  expect(api.mock.calls[0][1].signal.aborted).toBe(true)
  await act(async () => {
    pending.resolve({ items: [{ id: 'late' }], total: 1 })
    await loading
  })
  expect(result.current.historyDrawer).toBeNull()
  expect(result.current.historyLoading).toBe(false)
  expect(result.current.historyData.weights.items).toEqual([])
})

it('log filters advance today automatically and preserve a manually selected historical range', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(2026, 10, 4, 12, 0))
  const { result, unmount } = renderHook(useLogFilters)
  const end = result.current.logEnd
  act(() => vi.advanceTimersByTime(60000))
  expect(result.current.logEnd).toBe(end + 60000)
  act(() => {
    result.current.logRangeManuallyChanged.current = true
    result.current.setLogRange([1000, 2000])
  })
  act(() => {
    vi.advanceTimersByTime(120000)
    document.dispatchEvent(new Event('visibilitychange'))
  })
  expect([result.current.logStart, result.current.logEnd]).toEqual([1000, 2000])
  unmount()
  expect(vi.getTimerCount()).toBe(0)
})

it('a failed delete keeps confirmation open and a successful retry refreshes deployments', async () => {
  const refresh = vi.fn(),
    setToast = vi.fn()
  const { result } = renderHook(() => useDeploymentActions({ refresh, setToast, setDeps: vi.fn() }))
  act(() => result.current.deleteDeployment({ id: 'dep', name: 'test' }))
  api.mockRejectedValueOnce(new Error('delete failed')).mockResolvedValueOnce({})
  await act(() => result.current.confirmDelete())
  expect(result.current.deleteTarget.id).toBe('dep')
  expect(refresh).not.toHaveBeenCalled()
  await act(() => result.current.confirmDelete())
  expect(result.current.deleteTarget).toBeNull()
  expect(refresh).toHaveBeenCalledOnce()
})
