import React, { useEffect, useRef, useState } from 'react'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import { api } from './lib/managementApi'
import './NetworkSettings.css'

export default function NetworkSettings() {
  const [port, setPort] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [ports, setPorts] = useState([])
  const [refreshing, setRefreshing] = useState(true)
  const [recommendationError, setRecommendationError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const savedPort = useRef('')
  const refresh = async (signal) => {
    setRefreshing(true)
    setRecommendationError('')
    try {
      const value = await api('/network/recommendations', { signal })
      if (!signal?.aborted) setPorts(value.ports)
    } catch (error) {
      if (!signal?.aborted) setRecommendationError(error.message)
    } finally {
      if (!signal?.aborted) setRefreshing(false)
    }
  }
  useEffect(() => {
    const controller = new AbortController()
    api('/network', { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) {
          savedPort.current = String(value.port)
          setPort(savedPort.current)
          setLoaded(true)
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setMessage(error.message)
      })
    refresh(controller.signal)
    return () => controller.abort()
  }, [])
  const save = async (draft) => {
    if (!loaded || busy || draft === savedPort.current) return
    const value = Number(draft)
    if (!/^\d+$/.test(draft) || !Number.isInteger(value) || value < 1024 || value > 65535) {
      setMessage('请输入 1024～65535 的整数端口')
      return
    }
    setMessage('')
    setBusy(true)
    try {
      await api('/network', { method: 'PUT', body: JSON.stringify({ port: value }) })
      savedPort.current = draft
    } catch (error) {
      setMessage(error.message)
    } finally {
      setBusy(false)
    }
  }
  const choose = (value) => {
    setPort(value)
    setMessage('')
    save(value)
  }
  return (
    <div className="network-settings">
      <label htmlFor="gateway-port">下次启动端口</label>
      <div className="network-port-row">
        <div className="network-port-input">
          <span aria-hidden="true">127.0.0.1 :</span>
          <input
            id="gateway-port"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            maxLength={5}
            placeholder="输入端口"
            value={port}
            disabled={busy || !loaded}
            aria-describedby="gateway-port-hint"
            onChange={(e) => {
              setPort(e.target.value)
              setMessage('')
            }}
            onBlur={(e) => save(e.target.value)}
          />
          <button
            type="button"
            className="network-port-refresh"
            disabled={refreshing || busy}
            onClick={() => refresh()}
            aria-label="刷新推荐端口"
            title="检查并刷新推荐端口"
          >
            <RefreshCw size={14} className={refreshing ? 'spin' : ''} />
          </button>
        </div>
        {busy && (
          <LoaderCircle size={13} className="network-port-saving spin" aria-label="保存中" />
        )}
      </div>
      <div className="network-port-recommendations" aria-busy={refreshing}>
        <span>推荐端口</span>
        {ports.map((value) => (
          <button
            type="button"
            key={value}
            disabled={busy || refreshing || !loaded}
            onMouseDown={(e) => e.preventDefault()}
            className={Number(port) === value ? 'selected' : ''}
            aria-pressed={Number(port) === value}
            onClick={() => choose(String(value))}
          >
            {value}
          </button>
        ))}
      </div>
      {!refreshing && recommendationError && (
        <p className="field-hint network-port-error" role="alert">
          推荐获取失败：{recommendationError}，可点击刷新重试。
        </p>
      )}
      {!refreshing && !recommendationError && ports.length < 3 && (
        <p className="field-hint" role="status">
          候选池中仅有 {ports.length} 个可用端口，可手动输入或稍后刷新。
        </p>
      )}
      <p id="gateway-port-hint" className="field-hint">
        点击推荐端口立即保存；手动修改后离开输入框自动保存。端口被占用时，启动会自动选择并保存空闲端口。
      </p>
      {message && (
        <p role="status" className="field-hint">
          {message}
        </p>
      )}
    </div>
  )
}
