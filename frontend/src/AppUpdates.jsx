import React, { createContext, useContext, useEffect, useState, useSyncExternalStore } from 'react'
import { Channel, invoke, isTauri } from '@tauri-apps/api/core'
import { Download, RefreshCw, X } from 'lucide-react'
import { version } from '../package.json'
import { createUpdateController } from './lib/appUpdater'
import Drawer from './Drawer'
import './AppUpdates.css'

const Context = createContext(null)
const desktop = isTauri()
const storageKey = 'router-update-channel'
const channelName = channel => channel === 'stable' ? '稳定版' : '测试版'
function savedChannel() {
  try { return localStorage.getItem(storageKey) === 'stable' ? 'stable' : 'preview' } catch { return 'preview' }
}
const controller = createUpdateController({
  check: channel => invoke('check_app_update', { channel }),
  download: onProgress => {
    const onEvent = new Channel()
    onEvent.onmessage = onProgress
    return invoke('download_app_update', { onEvent })
  },
  install: () => invoke('install_app_update'),
}, savedChannel())
const busyPhases = ['checking', 'downloading', 'installing']

export function AppUpdatesProvider({ children }) {
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const [dismissed, setDismissed] = useState('')
  const [detailsOpen, setDetailsOpen] = useState(false)
  useEffect(() => {
    if (!desktop) return
    // Let the first application frame render; startup failures do not prevent
    // users from receiving a later repair release.
    const timer = setTimeout(() => controller.check(), 2000)
    return () => clearTimeout(timer)
  }, [])
  const changeChannel = channel => {
    try { localStorage.setItem(storageKey, channel) } catch { /* still usable this session */ }
    controller.check(channel)
  }
  const noticeKey = `${state.channel}:${state.update?.version}`
  const showNotice = desktop && state.update && ['available', 'downloading', 'ready'].includes(state.phase) && dismissed !== noticeKey
  return <Context.Provider value={{ state, changeChannel }}>
    {children}
    {showNotice && <aside className="update-notice" role="status" aria-live="polite">
      <Download size={17}/><span>{state.phase === 'ready' ? '更新已准备好' : state.phase === 'downloading' ? '正在下载更新' : `发现新版本 ${state.update.version}`}</span>
      <button className="button small" onClick={() => setDetailsOpen(true)}>查看更新</button>
      <button className="icon-button" aria-label="稍后处理更新" onClick={() => setDismissed(noticeKey)}><X size={15}/></button>
    </aside>}
    {detailsOpen && <Drawer title="应用更新" variant="modal" onClose={() => setDetailsOpen(false)}><div className="update-dialog-content"><UpdateControls/></div></Drawer>}
    {state.phase === 'installing' && <div className="update-installing" role="alert" aria-busy="true"><RefreshCw size={25}/><h2>正在重启并安装更新…</h2><p>正在停止本地服务，请稍候。安装完成后应用会重新打开。</p></div>}
  </Context.Provider>
}

export default function UpdateControls() {
  const { state, changeChannel } = useContext(Context)
  const busy = busyPhases.includes(state.phase)
  const progress = state.total > 0 ? Math.min(100, Math.floor(state.downloaded / state.total * 100)) : null
  return <div className="app-updates">
    <div className="setting-line"><span>当前版本</span><b>v{version}</b></div>
    {!desktop ? <p className="field-hint">请在桌面客户端中检查和安装更新。</p> : <>
      <label className="update-channel">更新通道<select aria-label="更新通道" value={state.channel} disabled={busy} onChange={e => changeChannel(e.target.value)}><option value="preview">测试版</option><option value="stable">稳定版</option></select></label>
      <p className="field-hint">启动时自动检查{channelName(state.channel)}，下载和安装由你确认。切换通道不会降级已安装的版本。</p>
      {state.channel === 'stable' && <p className="field-hint">稳定版尚未发布时，此通道无法获取更新；可切回测试版。</p>}
      {state.update && <div className="update-release"><b>新版本 v{state.update.version}</b>{state.update.date && <small>{new Date(state.update.date).toLocaleDateString('zh-CN')}</small>}<p>{state.update.notes || '此版本未提供更新说明。'}</p></div>}
      {state.phase === 'checking' && <p role="status">正在检查更新…</p>}
      {state.phase === 'current' && <p role="status">当前已是{channelName(state.channel)}通道的最新版本。</p>}
      {state.phase === 'downloading' && <div role="status"><progress aria-label="更新下载进度" max="100" {...(progress === null ? {} : { value: progress })}/><p>{progress === null ? `已下载 ${(state.downloaded / 1048576).toFixed(1)} MB` : `已下载 ${progress}%`}，完成后将验证更新签名。</p></div>}
      {(state.phase === 'ready' || (state.phase === 'error' && state.retry === 'install')) && <p className="field-hint">更新包已下载并通过验证。重启安装会短暂中断本地 API 服务，请等待当前请求完成后再继续。配置、密钥和历史记录会保留。</p>}
      {state.error && <p className="update-error" role="alert">{state.error}</p>}
      <div className="update-actions">
        <button className="button secondary small" disabled={busy} onClick={() => controller.check()}><RefreshCw size={13}/>检查更新</button>
        {state.phase === 'available' && <button className="button primary small" onClick={() => controller.download()}><Download size={13}/>下载更新</button>}
        {state.phase === 'ready' && <button className="button primary small" onClick={() => controller.install()}>重启并安装</button>}
        {state.phase === 'error' && state.retry !== 'check' && <button className="button primary small" onClick={() => controller.retry()}>{state.retry === 'install' ? '重试安装' : '重试下载'}</button>}
      </div>
    </>}
  </div>
}
