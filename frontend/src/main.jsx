import { ModelDetails, EventDetails } from './Details'
import { useDeploymentEditor } from './hooks/useDeploymentEditor'
import { emptyForm } from './shared'
import OverviewPage from './pages/OverviewPage'
import ModelsPage from './pages/ModelsPage'
import LogsPage from './pages/LogsPage'
import TuningPage from './pages/TuningPage'
import SettingsPage from './pages/SettingsPage'
import { useRouterData } from './hooks/useRouterData'
import { useToasts } from './hooks/useToasts'

import DesktopStartup from './DesktopStartup'
import { AppUpdatesProvider } from './AppUpdates'
import { version as APP_VERSION } from '../package.json'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

import {
  AlertTriangle,
  Bot,
  Check,
  Copy,
  Cpu,
  FileText,
  LayoutDashboard,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Radio,
  RefreshCw,
  Settings2,
  Sparkles,
} from 'lucide-react'
import { invoke, isTauri } from '@tauri-apps/api/core'

import './style.css'
import DeploymentDialog from './DeploymentDialog'

import Drawer from './Drawer'
import AuditRecordDialog from './AuditRecordDialog'

import { withMinimumDuration } from './lib/minimumDuration'
import { useTuning } from './hooks/useTuning'
import { useHistory } from './hooks/useHistory'
import { useLogFilters } from './hooks/useLogFilters'
import { useDeploymentActions } from './hooks/useDeploymentActions'

export function App() {
  const [tab, setTab] = useState('overview')

  const [selectedEvent, setSelectedEvent] = useState(null)

  const {
    logQuery,
    setLogQuery,
    logStart,
    logEnd,
    logPage,
    setLogPage,
    logsLive,
    setLogsLive,
    logRangeManuallyChanged,
    logStatusOpen,
    setLogStatusOpen,
    setLogRange,
  } = useLogFilters()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true)
  const [serviceMenuOpen, setServiceMenuOpen] = useState(false)
  const serviceMenuRef = useRef(null)
  const [refreshing, setRefreshing] = useState(false)
  const refreshingRef = useRef(false)
  const [toast, setToast] = useToasts()
  const {
    deps,
    setDeps,
    summary,
    events,
    settings,
    setSettings,
    bootstrap,
    overview,
    stats,
    hasMore,
    loading,
    refresh,
  } = useRouterData(
    { tab, start: logStart, end: logEnd, q: logQuery, page: logPage, live: logsLive },
    (message) => setToast(message, 'error'),
  )
  const {
    modal,
    setModal,
    editing,
    form,
    setForm,
    keyText,
    setKeyText,
    testingConnection,
    fetchingModels,
    availableModels,
    setAvailableModels,
    openCreate,
    openEdit,
    saveDeployment,
    saveProviderPresets,
    testDeployment,
    fetchModels,
    deploymentBusy,
  } = useDeploymentEditor({ refresh, setDeps, setToast, setSettings })
  const baseUrl = bootstrap?.base_url || ''
  const tuning = useTuning({ settings, setSettings, refresh, setToast, setTab })
  const history = useHistory({ settings, setToast })
  const {
    selectedModel,
    setSelectedModel,
    deleteTarget,
    setDeleteTarget,
    deleteDeployment,
    confirmDelete,
    toggleDeployment,
  } = useDeploymentActions({ refresh, setDeps, setToast })
  const manualRefresh = async () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    setRefreshing(true)
    try {
      await withMinimumDuration(() => refresh())
    } finally {
      refreshingRef.current = false
      setRefreshing(false)
    }
  }
  useEffect(() => {
    if (!serviceMenuOpen) return
    const closeOutside = (event) => {
      if (serviceMenuRef.current && !serviceMenuRef.current.contains(event.target))
        setServiceMenuOpen(false)
    }
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setServiceMenuOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOutside)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [serviceMenuOpen])

  const aliases = useMemo(() => [...new Set(deps.map((d) => d.alias))], [deps])
  const fmtTime = (value) =>
    value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
  const logFiltered = events
  const logStats = stats || {
    total: 0,
    failed: 0,
    rate: 0,
    avg: 0,
    p95: 0,
    tokens: 0,
    tokenEvents: 0,
    buckets: [],
  }
  const logPageCount = Math.max(1, Math.ceil(logStats.total / 25))
  const logPageRows = events
  const copy = async (value) => {
    try {
      await navigator.clipboard.writeText(value)
      setToast('已复制到剪贴板')
    } catch {
      setToast('复制失败，请重试', 'error')
    }
  }

  return (
    <div className={`shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Radio size={19} />
          </div>
          <div className="brand-copy">
            <b>模型路由器</b>
            <small>本地 AI 服务</small>
          </div>
        </div>
        <div className="side-caption">工作区</div>
        <nav>
          <button
            title="概览"
            className={tab === 'overview' ? 'active' : ''}
            onClick={() => setTab('overview')}
          >
            <LayoutDashboard size={17} />
            <span className="nav-label">概览</span>
          </button>
          <button
            title="模型路由"
            className={tab === 'models' ? 'active' : ''}
            onClick={() => setTab('models')}
          >
            <Bot size={17} />
            <span className="nav-label">模型路由</span>
            <span className="nav-count">{deps.length}</span>
          </button>
          <button
            title="请求日志"
            className={tab === 'logs' ? 'active' : ''}
            onClick={() => setTab('logs')}
          >
            <FileText size={17} />
            <span className="nav-label">请求日志</span>
          </button>
          <button
            title="自动调优"
            className={tab === 'tuning' ? 'active' : ''}
            onClick={() => setTab('tuning')}
          >
            <Sparkles size={17} />
            <span className="nav-label">自动调优</span>
          </button>
        </nav>
        <div className="side-caption settings-caption">偏好设置</div>
        <nav>
          <button
            title="系统设置"
            className={tab === 'settings' ? 'active' : ''}
            onClick={() => setTab('settings')}
          >
            <Settings2 size={17} />
            <span className="nav-label">系统设置</span>
          </button>
        </nav>
        <div className="side-spacer" />
        <div className="sidebar-status" ref={serviceMenuRef}>
          <span className="status-dot" />
          <div className="service-copy">
            <b>{bootstrap?.port_changed_from ? '端口已变更，请更新客户端' : '本地服务运行中'}</b>
            <small>{baseUrl ? new URL(baseUrl).host : '正在连接…'}</small>
          </div>
          <button
            className="service-menu-trigger"
            title="服务选项"
            onClick={() => setServiceMenuOpen((v) => !v)}
          >
            <MoreHorizontal size={17} />
          </button>
          {serviceMenuOpen && (
            <div className="service-menu">
              <button
                onClick={() => {
                  setTab('settings')
                  setServiceMenuOpen(false)
                }}
              >
                <Settings2 size={14} />
                服务与数据设置
              </button>
              {isTauri() && (
                <button
                  onClick={() => {
                    setServiceMenuOpen(false)
                    invoke('restart_backend')
                  }}
                >
                  <RefreshCw size={14} />
                  重启应用服务
                </button>
              )}
              <small>关闭窗口后，服务会留在系统托盘运行</small>
            </div>
          )}
        </div>
        <div className="profile">
          <div className="avatar">
            <Cpu size={14} />
          </div>
          <div className="profile-copy">
            <b>个人自用</b>
            <small>版本 {APP_VERSION}</small>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="top-leading">
            <button
              className="icon-button sidebar-toggle"
              title={sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'}
              onClick={() => setSidebarCollapsed((v) => !v)}
            >
              {sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
            </button>
            <div className="crumb">
              本地模型路由器 <span>/</span>{' '}
              <b>
                {
                  {
                    overview: '概览',
                    models: '模型路由',
                    logs: '请求日志',
                    tuning: '自动调优',
                    settings: '系统设置',
                  }[tab]
                }
              </b>
            </div>
          </div>
          <div className="top-actions">
            <button
              className="button secondary small"
              title="复制 API 地址"
              onClick={() => copy(baseUrl)}
            >
              <Copy size={13} />
              URL
            </button>
            <button
              className="button secondary small"
              title="复制本地 API Key"
              onClick={() => copy(bootstrap?.client_key)}
              disabled={!bootstrap?.client_key}
            >
              <Copy size={13} />
              密钥
            </button>
            <div className="top-avatar">L</div>
          </div>
        </header>
        <div className="content">
          {tab === 'overview' && (
            <OverviewPage
              {...{
                aliases,
                logEnd,
                logRangeManuallyChanged,
                logStart,
                overview,
                setLogPage,
                setLogRange,
                summary,
              }}
            />
          )}{' '}
          {tab === 'models' && (
            <ModelsPage
              {...{
                aliases,
                deleteDeployment,
                deps,
                manualRefresh,
                openCreate,
                openEdit,
                refreshing,
                setSelectedModel,
                setTab,
              }}
            />
          )}
          {tab === 'logs' && (
            <LogsPage
              {...{
                events,
                fmtTime,
                hasMore,
                loading,
                logEnd,
                logFiltered,
                logPage,
                logPageCount,
                logPageRows,
                logQuery,
                logRangeManuallyChanged,
                logStart,
                logStats,
                logsLive,
                logStatusOpen,
                setLogPage,
                setLogQuery,
                setLogRange,
                setLogStatusOpen,
                setLogsLive,
                setSelectedEvent,
              }}
            />
          )}
          {tab === 'tuning' && <TuningPage {...tuning} {...history} {...{ fmtTime, settings }} />}{' '}
          {tab === 'settings' && (
            <SettingsPage
              {...{
                baseUrl,
                bootstrap,
                saveProviderPresets,
                setToast,
                settings,
              }}
            />
          )}
        </div>
      </main>
      {selectedModel && (
        <ModelDetails
          {...{ baseUrl, copy, openEdit, selectedModel, setSelectedModel, toggleDeployment }}
        />
      )}
      {modal && (
        <DeploymentDialog
          editing={editing}
          form={form}
          setForm={setForm}
          emptyForm={emptyForm}
          keyText={keyText}
          setKeyText={setKeyText}
          availableModels={availableModels}
          setAvailableModels={setAvailableModels}
          deps={deps}
          providerPresets={settings?.provider_presets}
          busy={deploymentBusy}
          testingConnection={testingConnection}
          fetchingModels={fetchingModels}
          onSave={saveDeployment}
          onTest={testDeployment}
          onFetch={fetchModels}
          onClose={() => setModal(false)}
        />
      )}
      {selectedEvent && <EventDetails {...{ copy, fmtTime, selectedEvent, setSelectedEvent }} />}
      {tuning.auditRecord && (
        <AuditRecordDialog
          record={tuning.auditRecord}
          time={fmtTime(tuning.auditRecord.created_at)}
          onClose={() => tuning.setAuditRecord(null)}
        />
      )}
      {deleteTarget && (
        <Drawer title="删除模型部署" onClose={() => setDeleteTarget(null)}>
          <div className="drawer-content">
            <p>确定删除“{deleteTarget.name}”吗？历史请求日志会保留。</p>
            <button className="button secondary" onClick={() => setDeleteTarget(null)}>
              取消
            </button>
            <button className="button primary" onClick={confirmDelete}>
              确认删除
            </button>
          </div>
        </Drawer>
      )}
      {toast && (
        <div
          className={`toast${toast.type === 'error' ? ' copy-error' : toast.type === 'warn' ? ' warn' : ''}`}
          role={toast.type === 'error' ? 'alert' : 'status'}
          aria-live="polite"
        >
          {toast.type === 'success' ? <Check size={16} /> : <AlertTriangle size={16} />}{' '}
          {toast.message}
        </div>
      )}
    </div>
  )
}

const root = document.getElementById('root')
if (root)
  createRoot(root).render(
    <AppUpdatesProvider>
      <DesktopStartup>
        <App />
      </DesktopStartup>
    </AppUpdatesProvider>,
  )
