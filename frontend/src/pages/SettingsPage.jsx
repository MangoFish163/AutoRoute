import { useState } from 'react'

import RetentionSettings from '../RetentionSettings'
import CopyButton from '../CopyButton'
import NetworkSettings from '../NetworkSettings'

import UpdateControls from '../AppUpdates'
import { version as APP_VERSION } from '../../package.json'

import { AlertTriangle, Download, LockKeyhole, Radio } from 'lucide-react'

import { Badge } from '../components/ui/badge'

import ProviderPresets from '../ProviderPresets'

import { SettingsDrawer } from '../shared'

export default function SettingsPage({
  baseUrl,
  bootstrap,
  saveProviderPresets,
  setToast,
  settings,
}) {
  const [settingsSubtab, setSettingsSubtab] = useState('general')
  return (
    <>
      <div className="settings-subnav">
        <button
          className={settingsSubtab === 'general' ? 'active' : ''}
          onClick={() => setSettingsSubtab('general')}
        >
          系统偏好
        </button>
        <button
          className={settingsSubtab === 'providers' ? 'active' : ''}
          onClick={() => setSettingsSubtab('providers')}
        >
          厂商预设
        </button>
      </div>
      {settingsSubtab === 'providers' ? (
        settings ? (
          <ProviderPresets initial={settings.provider_presets} onSave={saveProviderPresets} />
        ) : (
          <p role="status">正在加载厂商预设…</p>
        )
      ) : (
        <>
          <div className="page-heading">
            <div>
              <div className="eyebrow">PREFERENCES</div>
              <h1>系统设置</h1>
              <p>查看本地服务接入信息与数据保护配置。</p>
            </div>
            <Badge tone="green" dot>
              仅本机监听
            </Badge>
          </div>
          <div className="settings-grid">
            <SettingsDrawer title="本地网关" description="客户端连接本地服务使用" icon={Radio}>
              <label>Base URL</label>
              <div className="copy-field">
                <code>{baseUrl}</code>
                <CopyButton value={baseUrl} label="复制 API 地址" />
              </div>
              {bootstrap?.port_changed_from && (
                <p role="status" className="field-hint">
                  原端口 {bootstrap.port_changed_from} 无法使用，已切换并保存为{' '}
                  {new URL(baseUrl).port}。请复制上方地址更新客户端。
                </p>
              )}
              <label>本地 API Key</label>
              <div className="copy-field">
                <code>••••••••••••••••••••••••••••</code>
                <CopyButton value={bootstrap?.client_key} label="复制本地 API Key" />
              </div>
              <p className="field-hint">
                客户端请求使用 Bearer Token。初次启动时自动生成，保存在本地数据目录。
              </p>
              <NetworkSettings />
            </SettingsDrawer>
            <SettingsDrawer
              title="数据与隐私"
              description="本地数据库及日志保护"
              icon={LockKeyhole}
              tone="purple"
            >
              <div className="setting-line">
                <span>数据库</span>
                <Badge>SQLite</Badge>
              </div>
              <div className="setting-line">
                <span>请求正文记录</span>
                <Badge tone="green">关闭</Badge>
              </div>
              <div className="setting-line">
                <span>上游 Token 日志</span>
                <Badge tone="green">脱敏</Badge>
              </div>
              <div className="setting-line">
                <span>Windows Token 加密</span>
                <Badge tone="green">DPAPI</Badge>
              </div>
              <div className="setting-line">
                <span>数据目录</span>
                <code>{bootstrap?.data_dir || '%USERPROFILE%\\.local-model-router'}</code>
              </div>
              <RetentionSettings onError={(message) => setToast(message, 'error')} />
              <p className="field-hint">
                API Token 使用当前 Windows 用户的 DPAPI 加密；请求正文默认不写入日志。
              </p>
            </SettingsDrawer>
            <SettingsDrawer title="应用更新" description="版本检查与客户端升级" icon={Download}>
              <UpdateControls />
            </SettingsDrawer>
            <SettingsDrawer
              title="协议能力"
              description="当前初版实现范围"
              badge={<Badge tone="blue">v{APP_VERSION}</Badge>}
            >
              <div className="protocol-row">
                <div className="protocol-logo openai">O</div>
                <div>
                  <b>OpenAI Chat Completions</b>
                  <small>/v1/chat/completions · /v1/models</small>
                </div>
                <Badge tone="green" dot>
                  可用
                </Badge>
              </div>
              <div className="protocol-row">
                <div className="protocol-logo anthropic">A</div>
                <div>
                  <b>Anthropic Messages</b>
                  <small>/v1/messages · 同协议流式透传与非流式适配</small>
                </div>
                <Badge tone="amber" dot>
                  基础支持
                </Badge>
              </div>
              <div className="notice">
                <AlertTriangle size={15} />
                支持 Chat Completions 转接上游 Responses（含流式）；Anthropic
                跨协议流式与完整工具调用映射尚未支持。
              </div>
            </SettingsDrawer>
          </div>
        </>
      )}
    </>
  )
}
