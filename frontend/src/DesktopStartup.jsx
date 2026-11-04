import React, { useEffect, useState } from 'react'
import { invoke, isTauri } from '@tauri-apps/api/core'
import { Radio } from 'lucide-react'
import './DesktopStartup.css'

export default function DesktopStartup({ children }) {
  const desktop = isTauri()
  const [status, setStatus] = useState({ phase: desktop ? 'starting' : 'ready' })
  const [actionError, setActionError] = useState('')
  useEffect(() => {
    if (!desktop) return
    let active = true,
      timer
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
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [desktop])
  if (status.phase === 'ready') return children
  if (status.phase !== 'error')
    return (
      <main className="desktop-startup" role="status" aria-live="polite" aria-busy="true">
        <div className="desktop-startup-loader" aria-hidden="true">
          <span className="desktop-startup-ring" />
          <span className="desktop-startup-icon">
            <Radio size={26} strokeWidth={1.8} />
          </span>
        </div>
        <span className="desktop-startup-label">正在启动本地服务…</span>
      </main>
    )
  const action = async (name) => {
    try {
      setActionError('')
      await invoke(name)
    } catch (error) {
      setActionError(String(error))
    }
  }
  return (
    <main className="desktop-startup">
      <section style={{ maxWidth: 540 }} role="status" aria-live="polite">
        <h1>本地服务未能启动</h1>
        <p>{status.error}</p>
        <div style={{ display: 'flex', gap: 12 }}>
          <button className="button primary" onClick={() => action('retry_backend')}>
            重试
          </button>
          <button className="button" onClick={() => action('open_log_directory')}>
            打开日志目录
          </button>
        </div>
        {actionError && <p role="alert">{actionError}</p>}
      </section>
    </main>
  )
}
