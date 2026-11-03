import React, { useEffect, useState } from 'react'
import { invoke, isTauri } from '@tauri-apps/api/core'

export default function DesktopStartup({ children }) {
  const desktop = isTauri()
  const [status, setStatus] = useState({ phase: desktop ? 'starting' : 'ready' })
  const [actionError, setActionError] = useState('')
  useEffect(() => {
    if (!desktop) return
    let active = true, timer
    const poll = async () => {
      try {
        const next = await invoke('startup_status')
        if (!active) return
        setStatus(next)
        timer = setTimeout(poll, next.phase === 'ready' ? 3000 : 500)
      } catch (error) {
        if (active) setStatus({ phase: 'error', error: String(error) })
      }
    }
    poll()
    return () => { active = false; clearTimeout(timer) }
  }, [desktop])
  if (status.phase === 'ready') return children
  const action = async name => {
    try { setActionError(''); await invoke(name) } catch (error) { setActionError(String(error)) }
  }
  return <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 32 }}>
    <section style={{ maxWidth: 540 }} role="status" aria-live="polite">
      <h1>{status.phase === 'error' ? '本地服务未能启动' : '正在启动本地服务…'}</h1>
      <p>{status.error || '首次启动可能需要一些时间，服务就绪后将自动进入管理面板。'}</p>
      {status.phase === 'error' && <div style={{ display: 'flex', gap: 12 }}>
        <button className="button primary" onClick={() => action('retry_backend')}>重试</button>
        <button className="button" onClick={() => action('open_log_directory')}>打开日志目录</button>
      </div>}
      {actionError && <p role="alert">{actionError}</p>}
    </section>
  </main>
}
