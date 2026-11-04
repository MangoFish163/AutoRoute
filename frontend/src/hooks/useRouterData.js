import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, watchManagementEvents } from '../lib/managementApi'

export function useRouterData(params, onError) {
  // Decide when the range changes, so a current window keeps rolling after a
  // minute, while a historical window never grows during polling or SSE.
  const currentWindow = useMemo(() => {
    const now = Date.now()
    return params.end >= now - 60000 && params.start <= now && now - params.start <= 86400000
  }, [params.start, params.end])
  const rolling = params.tab === 'logs' && params.live && currentWindow
  const latest = useRef(params)
  latest.current = { ...params, rolling }
  const error = useRef(onError)
  error.current = onError
  const [data, setData] = useState({
    deps: [],
    events: [],
    summary: null,
    settings: null,
    bootstrap: null,
    overview: null,
    stats: null,
    hasMore: false,
    loading: false,
  })
  const current = useRef(data)
  current.current = data
  const request = useRef(null)
  const version = useRef(0)
  const cursors = useRef([null])
  const filter = useRef('')
  const refresh = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const revision = ++version.current
    setData((old) => ({ ...old, loading: true }))
    const { tab, start, end, q, page, rolling } = latest.current
    const signature = JSON.stringify([start, end, q])
    if (filter.current !== signature) {
      filter.current = signature
      cursors.current = [null]
    }
    const range = new URLSearchParams({
      start: Math.floor(start),
      end: Math.floor(rolling ? Date.now() : end),
      q,
    })
    const get = (path) => api(path, { signal: controller.signal })
    try {
      const tasks = []
      const fetch = (key, path) => tasks.push(get(path).then((value) => [key, value]))
      if (!current.current.bootstrap || tab === 'settings') fetch('bootstrap', '/bootstrap')
      if (!current.current.settings || ['models', 'settings', 'tuning'].includes(tab))
        fetch('settings', '/settings')
      if (!current.current.bootstrap || ['models', 'overview'].includes(tab))
        fetch('deps', '/deployments')
      if (['overview', 'models'].includes(tab)) fetch('summary', '/summary')
      if (tab === 'overview') fetch('overview', `/overview?${range}`)
      if (tab === 'logs') {
        fetch('stats', `/events/stats?${range}`)
        range.set('limit', '25')
        if (cursors.current[page - 1]) range.set('cursor', cursors.current[page - 1])
        fetch('logPage', `/events/page?${range}`)
      }
      const next = Object.fromEntries(await Promise.all(tasks))
      if (revision !== version.current || controller.signal.aborted) return false
      if (next.logPage) {
        cursors.current[page] = next.logPage.next_cursor
        next.events = next.logPage.items
        next.hasMore = next.logPage.has_more
        delete next.logPage
      }
      setData((old) => ({ ...old, ...next, loading: false }))
      return true
    } catch (e) {
      if (revision === version.current) {
        setData((old) => ({ ...old, loading: false }))
        if (e.name !== 'AbortError') error.current(e.message)
      }
      return false
    }
  }, [])
  useEffect(() => {
    let timer, subscription
    const stop = () => {
      clearInterval(timer)
      subscription?.abort()
      request.current?.abort()
      version.current++
    }
    const start = () => {
      stop()
      if (document.hidden) return
      refresh()
      timer = setInterval(refresh, 12000)
      const p = latest.current
      if (p.rolling) {
        subscription = new AbortController()
        let pending = false
        let dirty = false
        const notify = async () => {
          if (pending) {
            dirty = true
            return
          }
          pending = true
          do {
            dirty = false
            await refresh()
          } while (dirty && !subscription.signal.aborted)
          pending = false
        }
        watchManagementEvents(
          `/events/stream?start=${Math.floor(p.start)}&end=${Math.floor(p.end)}&live=true`,
          notify,
          subscription.signal,
        )
      }
    }
    start()
    document.addEventListener('visibilitychange', start)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', start)
    }
  }, [params.tab, params.start, params.end, params.q, params.page, params.live, refresh])
  return {
    ...data,
    refresh,
    setDeps: (value) =>
      setData((old) => ({ ...old, deps: typeof value === 'function' ? value(old.deps) : value })),
    setSettings: (value) =>
      setData((old) => ({
        ...old,
        settings: typeof value === 'function' ? value(old.settings) : value,
      })),
  }
}

export function useDirtyForm(remote, initial) {
  const [form, update] = useState(initial)
  const dirty = useRef(false)
  useEffect(() => {
    if (remote && !dirty.current) update((old) => ({ ...old, ...remote, api_key: '' }))
  }, [remote])
  const set = useCallback((value) => {
    dirty.current = true
    update(value)
  }, [])
  const saved = useCallback((value) => {
    dirty.current = false
    if (value) update((old) => ({ ...old, ...value, api_key: '' }))
  }, [])
  return [form, set, saved]
}
