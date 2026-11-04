import React, { useEffect, useRef, useState } from 'react'
import { api } from './lib/managementApi'
import { FormSelect } from './shared'

export default function RetentionSettings({ onError }) {
  const error = useRef(onError)
  error.current = onError
  const [days, setDays] = useState(0)
  const [busy, setBusy] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    api('/settings/retention', { signal: controller.signal })
      .then((x) => setDays(x.retention_days))
      .catch((e) => {
        if (e.name !== 'AbortError') error.current(e.message)
      })
      .finally(() => setBusy(false))
    return () => controller.abort()
  }, [])
  const save = async (e) => {
    const next = Number(e.target.value)
    setBusy(true)
    try {
      const result = await api('/settings/retention', {
        method: 'PUT',
        body: JSON.stringify({ retention_days: next }),
      })
      setDays(result.retention_days)
    } catch (e) {
      error.current(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <label className="setup-field">
      历史记录保留
      <FormSelect
        ariaLabel="历史记录保留"
        value={days}
        onChange={(value) => save({ target: { value } })}
        options={[
          [0, '关闭自动清理'],
          [30, '30 天'],
          [90, '90 天'],
          [180, '180 天'],
        ]}
        disabled={busy}
      />
      <small>启用后在启动及每日清理过期请求、权重和 AI 历史；部署统计与冷却状态保留。</small>
    </label>
  )
}
