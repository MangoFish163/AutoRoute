import { invoke, isTauri } from '@tauri-apps/api/core'
import { consumeEvents } from './serverEvents.js'

const desktop = isTauri()


// Browser development only: the launcher supplies a fragment, never a query
// parameter. Remove it immediately; session storage allows a development reload.
if (!desktop) {
  const token = new URLSearchParams(window.location.hash.slice(1)).get('management_token')
  if (token) {
    sessionStorage.setItem('router-management-token', token)
    history.replaceState(null, '', window.location.pathname + window.location.search)
  }
}

export async function managementFetch(path, options = {}) {
  const token = desktop
    ? await invoke('management_token')
    : sessionStorage.getItem('router-management-token')
  if (!token) throw new Error('管理连接未初始化，请通过应用启动入口重新打开。')
  const headers = new Headers(options.headers)
  headers.set('X-Management-Token', token)
  const base = desktop ? (await invoke('backend_url')) + '/api' : '/api'
  return fetch(base + path, { ...options, headers })
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  const res = await managementFetch(path, { ...options, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail?.error?.message || body.detail || body.error?.message || `请求失败 (${res.status})`)
  }
  return res.json()
}

export async function watchManagementEvents(path, onNew, signal) {
  while (!signal.aborted) {
    try {
      const response = await managementFetch(path, { signal, headers: { Accept: 'text/event-stream' } })
      if (!response.ok) {
        await response.body?.cancel()
        throw new Error('无法连接实时日志')
      }
      await consumeEvents(response.body, onNew)
    } catch {
      if (signal.aborted) return
    }
    await new Promise(resolve => {
      const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
      const timer = setTimeout(finish, 3000)
      signal.addEventListener('abort', finish, { once: true })
      if (signal.aborted) finish()
    })
  }
}
