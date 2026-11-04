import { useState } from 'react'
import { api } from '../lib/managementApi'
import { emptyForm } from '../shared'

export function useDeploymentEditor({ refresh, setDeps, setToast, setSettings }) {
  const [busy, setBusy] = useState(false)
  const [modal, setModal] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(emptyForm)
  const [keyText, setKeyText] = useState('')
  const [testingConnection, setTestingConnection] = useState(false)
  const [fetchingModels, setFetchingModels] = useState(false)
  const [availableModels, setAvailableModels] = useState([])
  const openCreate = () => {
    setEditing(null)
    setForm(emptyForm)
    setKeyText('')
    setAvailableModels([])
    setModal(true)
  }
  const openEdit = (dep) => {
    setEditing(dep)
    setForm({ ...dep, api_keys: [] })
    setKeyText('')
    setAvailableModels([])
    setModal(true)
  }
  const saveDeployment = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const payload = {
        ...form,
        name: form.name.trim() || form.upstream_model.trim(),
        alias: form.alias.trim() || form.upstream_model.trim(),
        upstream_model: form.upstream_model.trim(),
        base_url: form.base_url.trim().replace(/\/+$/, ''),
        api_keys: keyText
          .split(/\r?\n/)
          .map((x) => x.trim())
          .filter(Boolean),
      }
      if (editing && !keyText.trim()) payload.api_keys = [] // API merge preserves existing credentials when no new keys supplied.
      const saved = await api(editing ? `/deployments/${editing.id}` : '/deployments', {
        method: editing ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      })
      setDeps((old) =>
        editing ? old.map((d) => (d.id === saved.id ? saved : d)) : [...old, saved],
      )
      setModal(false)
      setToast(editing ? '已保存部署，运行状态已重置' : '模型部署已添加')
      await refresh()
    } catch (err) {
      setToast(err.message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const saveProviderPresets = async (presets) => {
    const result = await api('/settings/provider-presets', {
      method: 'PUT',
      body: JSON.stringify({ presets }),
    })
    setSettings((old) => ({ ...old, provider_presets: result.presets }))
    return result
  }
  const checkConnection = async (fetch) => {
    const token =
      keyText
        .split(/\r?\n/)
        .map((x) => x.trim())
        .find(Boolean) || (editing ? '__saved__' : '')
    if (!form.base_url || !token || (!fetch && !form.upstream_model)) {
      setToast(
        fetch
          ? '请先填写 API Base URL 和至少一个 API Token'
          : '请先填写 API Base URL、模型 ID 和至少一个 API Token',
        'warn',
      )
      return
    }
    const setWorking = fetch ? setFetchingModels : setTestingConnection
    setWorking(true)
    try {
      const result = await api('/deployments/test-connection', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          api_keys: undefined,
          api_key: token,
          deployment_id: editing?.id,
        }),
      })
      if (fetch) setAvailableModels(result.models || [])
      setToast(result.message || '连接成功')
    } catch (e) {
      setToast(e.message, 'error')
    } finally {
      setWorking(false)
    }
  }
  const testDeployment = () => checkConnection(false)
  const fetchModels = () => checkConnection(true)

  return {
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
    deploymentBusy: busy,
  }
}
