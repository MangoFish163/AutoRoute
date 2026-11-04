import { useState } from 'react'
import { api } from '../lib/managementApi'
import { useDirtyForm } from './useRouterData'

export function useTuning({ settings, setSettings, refresh, setToast, setTab }) {
  const [busy, setBusy] = useState(false)
  const [auditRecord, setAuditRecord] = useState(null)
  const [tuningSettingsOpen, setTuningSettingsOpen] = useState(false)
  const [aiForm, setAiForm, aiSaved] = useDirtyForm(settings?.ai, {
    enabled: false,
    provider: 'openai',
    base_url: 'https://api.openai.com/v1',
    model: '',
    api_key: '',
    key_saved: false,
  })
  const [ruleForm, setRuleForm, rulesSaved] = useDirtyForm(settings?.rules, {
    enabled: true,
    failure_threshold: 3,
    cooldown_seconds: 90,
    min_auto_factor: 0.25,
    max_auto_factor: 1.5,
  })
  const analyze = async () => {
    setBusy(true)
    try {
      const result = await api('/ai/analyze', { method: 'POST', body: JSON.stringify({}) })
      await refresh()
      setTab('tuning')
      if (result.status === 'failed') {
        setToast(
          `AI 分析失败：${result.report?.analysis_error || '上游服务调用失败'}；本地指标快照已保存`,
          'error',
        )
      } else {
        setToast(
          result.status === 'complete' && aiForm.enabled
            ? 'AI 综合分析已完成'
            : '已生成指标分析快照',
        )
      }
    } catch (e) {
      setToast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const tune = async () => {
    setBusy(true)
    try {
      const result = await api('/ai/tune', { method: 'POST', body: JSON.stringify({}) })
      await refresh()
      setToast(
        result.status === 'no_change'
          ? 'AI 建议无需调整权重'
          : `已应用 ${result.changes.length} 项 AI 权重建议`,
      )
    } catch (e) {
      setToast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const persistAi = async (config) => {
    const value = await api('/settings/ai', { method: 'PUT', body: JSON.stringify(config) })
    aiSaved(value)
    return value
  }
  const saveAi = async (e) => {
    e.preventDefault()
    if (aiForm.enabled && !aiForm.model?.trim()) {
      setToast('已启用 AI 分析：请填写分析模型后保存', 'warn')
      return
    }
    setBusy(true)
    try {
      await persistAi(aiForm)
      await refresh()
      setToast('AI 分析配置已保存')
    } catch (err) {
      setToast(err.message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const saveRules = async (ev) => {
    ev.preventDefault()
    try {
      const result = await api('/settings/rules', { method: 'PUT', body: JSON.stringify(ruleForm) })
      rulesSaved(result)
      setSettings((old) => ({ ...old, rules: result }))
      setToast('路由规则已保存')
    } catch (e) {
      setToast(e.message, 'error')
    }
  }
  const toggleAi = async (enabled) => {
    if (enabled && !aiForm.model?.trim()) {
      setToast('请先在调优配置中填写分析模型，再启用 AI 分析', 'warn')
      return
    }
    const previous = aiForm.enabled
    const next = { ...aiForm, enabled }
    setAiForm(next)
    setBusy(true)
    try {
      await persistAi(next)
      setToast(enabled ? 'AI 分析已启用' : 'AI 分析已停用')
    } catch (e) {
      setAiForm((old) => ({ ...old, enabled: previous }))
      setToast(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const toggleRules = async (enabled) => {
    const previous = ruleForm.enabled
    const next = { ...ruleForm, enabled }
    setRuleForm(next)
    try {
      const saved = await api('/settings/rules', { method: 'PUT', body: JSON.stringify(next) })
      rulesSaved(saved)
      setSettings((old) => (old ? { ...old, rules: saved } : old))
      setToast(enabled ? '规则路由已启用' : '规则路由已停用')
    } catch (e) {
      setRuleForm((old) => ({ ...old, enabled: previous }))
      setToast(e.message, 'error')
    }
  }
  return {
    busy,
    aiForm,
    setAiForm,
    ruleForm,
    setRuleForm,
    auditRecord,
    setAuditRecord,
    tuningSettingsOpen,
    setTuningSettingsOpen,
    analyze,
    tune,
    saveAi,
    saveRules,
    toggleAi,
    toggleRules,
  }
}
