import React, { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Copy, LoaderCircle } from 'lucide-react'
import './CopyButton.css'

export default function CopyButton({ value, label = '复制' }) {
  const [status, setStatus] = useState('idle')
  const timer = useRef(null)
  const attempt = useRef(0)
  const pending = useRef(false)
  useEffect(() => () => { clearTimeout(timer.current); attempt.current++ }, [])
  const copy = async () => {
    if (pending.current || !value) return
    pending.current = true
    const current = ++attempt.current
    clearTimeout(timer.current)
    setStatus('pending')
    try {
      await navigator.clipboard.writeText(value)
      if (attempt.current === current) setStatus('success')
    } catch {
      if (attempt.current === current) setStatus('error')
    } finally {
      pending.current = false
      if (attempt.current === current) timer.current = setTimeout(() => setStatus('idle'), 2200)
    }
  }
  const text = { idle: '复制', pending: '复制中', success: '已复制', error: '复制失败' }[status]
  const Icon = { idle: Copy, pending: LoaderCircle, success: Check, error: AlertTriangle }[status]
  return <button type="button" className={`copy-feedback-button is-${status}`} disabled={!value || status === 'pending'} onClick={copy} aria-label={`${label}${status === 'idle' ? '' : `：${text}`}`} title={status === 'error' ? '未能写入剪贴板，请点击重试' : label}>
    <Icon size={14} className={status === 'pending' ? 'spin' : undefined}/>
    <span role="status" aria-live="polite" aria-atomic="true">{text}</span>
  </button>
}
