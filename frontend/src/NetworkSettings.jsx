import React, { useEffect, useState } from 'react'
import { Check, LoaderCircle, RefreshCw } from 'lucide-react'
import { api } from './lib/managementApi'
import './NetworkSettings.css'

export default function NetworkSettings() {
  const [port, setPort] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [ports, setPorts] = useState([])
  const [refreshing, setRefreshing] = useState(true)
  const [recommendationError, setRecommendationError] = useState('')
  const refresh = async signal => {
    setRefreshing(true)
    setRecommendationError('')
    setPorts([])
    try {
      const value = await api('/network/recommendations', { signal })
      if (!signal?.aborted) setPorts(value.ports)
    } catch (error) {
      if (!signal?.aborted) setRecommendationError(error.message)
    } finally { if (!signal?.aborted) setRefreshing(false) }
  }
  useEffect(() => {
    const controller = new AbortController()
    api('/network', { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setPort(String(value.port)) }).catch(error => { if (!controller.signal.aborted) setMessage(error.message) })
    refresh(controller.signal)
    return () => controller.abort()
  }, [])
  const save = async () => {
    const value = Number(port)
    if (!/^\d+$/.test(port) || !Number.isInteger(value) || value < 1024 || value > 65535) { setMessage('请输入 1024～65535 的整数端口'); return }
    setBusy(true)
    try {
      await api('/network', { method: 'PUT', body: JSON.stringify({ port: value }) })
      setMessage('已保存，下次启动应用时生效。重启后请复制实际 API 地址更新客户端。')
    } catch (error) { setMessage(error.message) } finally { setBusy(false) }
  }
  const choose = value => { setPort(value); setMessage('') }
  return <div className="network-settings">
    <label htmlFor="gateway-port">下次启动端口</label>
    <div className="network-port-row">
      <div className="network-port-input">
        <span aria-hidden="true">127.0.0.1 :</span>
        <input id="gateway-port" type="text" inputMode="numeric" autoComplete="off" maxLength={5} placeholder="输入端口" value={port} disabled={busy} aria-describedby="gateway-port-hint" onChange={e => choose(e.target.value)} />
        <button type="button" className="network-port-refresh" disabled={refreshing || busy} onClick={() => refresh()} aria-label="刷新推荐端口" title="检查并刷新推荐端口"><RefreshCw size={14} className={refreshing ? 'spin' : ''}/></button>
      </div>
      <button type="button" className="button secondary small network-port-save" disabled={busy || !port} onClick={save}>{busy ? <LoaderCircle size={13} className="spin"/> : <Check size={13}/>}保存</button>
    </div>
    <div className="network-port-recommendations" aria-busy={refreshing}>
      <span>{refreshing ? '检查可用端口…' : '推荐端口'}</span>
      {!refreshing && ports.map(value => <button type="button" key={value} disabled={busy} className={Number(port) === value ? 'selected' : ''} aria-pressed={Number(port) === value} onClick={() => choose(String(value))}>{value}</button>)}
    </div>
    {!refreshing && recommendationError && <p className="field-hint network-port-error" role="alert">推荐获取失败：{recommendationError}，可点击刷新重试。</p>}
    {!refreshing && !recommendationError && ports.length < 3 && <p className="field-hint" role="status">候选池中仅有 {ports.length} 个可用端口，可手动输入或稍后刷新。</p>}
    <p id="gateway-port-hint" className="field-hint">推荐值已检查当前可用性，点击填入后需保存。端口被占用时，启动会自动选择并保存空闲端口。</p>
    {message && <p role="status" className="field-hint">{message}</p>}
  </div>
}
