import React, { useEffect, useRef, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'

const newProvider = {name:'新厂商',avatar_url:'',base_url:'',models_url:'',provider:'openai',base_weight:1,rpm:0,max_input_tokens:0,max_output_tokens:0,timeout_seconds:90,multimodal:true,thinking_mode:false,enabled:true,proxy_url:'',use_system_proxy:true,note:''}

const providerSlug = name => (name || '').trim().toLowerCase().replace(/\s+/g, '-')

function PresetAvatar({url,name}) {
  const [failed,setFailed] = useState(false)
  const [builtinFailed,setBuiltinFailed] = useState(false)
  const builtin = providerSlug(name)
  useEffect(() => { setFailed(false); setBuiltinFailed(false) }, [url,name])
  const image = url && !failed ? url : !builtinFailed && builtin ? `/images/providers/${builtin}.webp` : null
  return <i className="preset-avatar">{image ? <img src={image} alt="" onError={() => url && !failed ? setFailed(true) : setBuiltinFailed(true)}/> : Array.from(name || '?')[0]}</i>
}

const tokenShortcuts = {max_input_tokens:[128000,256000,512000,1000000],max_output_tokens:[4000,16000,32000,128000]}
const weightPresets = [['低权重',0.5],['无偏好',1],['高权重',2]]

export default function ProviderPresets({initial={},onSave}) {
  const [items,setItems] = useState(() => ({...initial}))
  const [selected,setSelected] = useState(() => Object.keys({...initial})[0])
  const [saveState,setSaveState] = useState('saved')
  const [saveError,setSaveError] = useState('')
  const [deleteTarget,setDeleteTarget] = useState(null)
  const [deleteCode,setDeleteCode] = useState('')
  const [deleteInput,setDeleteInput] = useState('')
  const [flashId,setFlashId] = useState('')
  const mounted = useRef(false)
  const saveQueue = useRef(Promise.resolve())
  const saveVersion = useRef(0)
  const flashTimer = useRef(null)
  const savedTimer = useRef(null)
  const p = items[selected] || {}
  const patch = (key,value) => setItems(current => ({...current,[selected]:{...current[selected],[key]:value}}))

  const addProvider = () => {
    const duplicate = Object.entries(items).find(([,value]) => (value.name||'').trim().toLocaleLowerCase()==='新厂商'.toLocaleLowerCase())
    if (duplicate) {
      setSelected(duplicate[0])
      setFlashId('')
      requestAnimationFrame(() => setFlashId(duplicate[0]))
      clearTimeout(flashTimer.current)
      flashTimer.current = setTimeout(() => setFlashId(''),1500)
      return
    }
    const id = `provider-${Date.now()}`
    setItems(current => ({...current,[id]:{...newProvider}}))
    setSelected(id)
  }

  const requestDelete = () => {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    const bytes = crypto.getRandomValues(new Uint8Array(6))
    setDeleteCode(Array.from(bytes,value => chars[value % chars.length]).join(''))
    setDeleteInput('')
    setDeleteTarget(selected)
  }

  const confirmDelete = () => {
    if (!deleteTarget || deleteInput.trim().toUpperCase() !== deleteCode) return
    setItems(current => {const next={...current};delete next[deleteTarget];return next})
    setSelected(Object.keys(items).find(id => id!==deleteTarget))
    setDeleteTarget(null)
  }

  const save = async (snapshot=items,version=++saveVersion.current) => {
    setSaveState('saving')
    setSaveError('')
    saveQueue.current = saveQueue.current.catch(() => {}).then(() => onSave(snapshot))
    try {
      await saveQueue.current
      if (version === saveVersion.current) {
        setSaveState('saved')
        clearTimeout(savedTimer.current)
        savedTimer.current = setTimeout(() => {
          setSaveState(current => current === 'saved' ? '' : current)
        },3000)
      }
    } catch (error) {
      if (version === saveVersion.current) {
        setSaveError(error?.message || '未知错误')
        setSaveState('error')
      }
    }
  }

  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return }
    const version = ++saveVersion.current
    setSaveState('dirty')
    const timer = setTimeout(() => save(items,version),700)
    return () => clearTimeout(timer)
  },[items])

  useEffect(() => () => clearTimeout(savedTimer.current),[])

  const status = {saved:'已保存',dirty:'停止编辑后自动保存',saving:'正在保存…',error:`自动保存失败：${saveError}`}[saveState] || ''

  return <>
    <div className="page-heading">
      <div><div className="eyebrow">PREFERENCES</div><h1>厂商预设</h1><p>管理添加模型时使用的服务商默认参数。</p></div>
      <div className="preset-save-actions"><small role="status">{status}</small></div>
    </div>
    <div className="preset-layout">
      <nav className="panel preset-list">
        {Object.entries(items).map(([id,value]) => <button key={id} className={`${id===selected?'active':''} ${id===flashId?'preset-duplicate-flash':''}`} onClick={() => setSelected(id)}><PresetAvatar url={value.avatar_url} name={value.name}/>{value.name||id}</button>)}
        <button className="preset-add" onClick={addProvider}><Plus size={15}/>添加厂商</button>
        <button className="preset-delete" disabled={Object.keys(items).length<=1} onClick={requestDelete}><Trash2 size={14}/>删除当前预设</button>
      </nav>
      <section className="panel preset-editor"><div className="preset-grid"><label>API 格式<select value={p.provider||'openai'} onChange={event=>patch('provider',event.target.value)}><option value="openai">OpenAI Chat Completions</option><option value="anthropic">Anthropic Messages</option><option value="responses">Responses（豆包 / OpenAI）</option></select></label>
        { [['name','厂商名称','text'],['avatar_url','头像链接','url'],['base_url','Base URL','text'],['models_url','拉取模型地址','text'],['rpm','RPM','number']].map(([key,label,type]) => <label key={key}>{label}<input type={type} value={p[key]??''} onChange={event => patch(key,type==='number'?Number(event.target.value):event.target.value)}/></label> ) }
        <label className="preset-token-field"><span>基础权重<span className="preset-token-shortcuts">{weightPresets.map(([label,value]) => <button key={label} type="button" onClick={() => patch('base_weight',value)}>{label}</button>)}</span></span><input type="number" min="0" max="100" step="0.1" value={p.base_weight??1} onChange={event => patch('base_weight',Number(event.target.value))}/></label>
        { [['max_input_tokens','最大输入 Token'],['max_output_tokens','最大输出 Token']].map(([key,label]) => <label key={key} className="preset-token-field"><span>{label}<span className="preset-token-shortcuts">{tokenShortcuts[key].map(value => <button key={value} type="button" onClick={() => patch(key,value)}>{value>=1000000?`${value/1000000}M`:value>=1000?`${value/1000}k`:value}</button>)}</span></span><input type="number" min="0" value={p[key]??0} onChange={event => patch(key,Number(event.target.value))}/></label> ) }
        { [['timeout_seconds','请求超时（秒）','number'],['proxy_url','自定义代理','text']].map(([key,label,type]) => <label key={key}>{label}<input type={type} value={p[key]??''} onChange={event => patch(key,type==='number'?Number(event.target.value):event.target.value)}/></label> ) }
        <label className="preset-wide">备注<textarea value={p.note||''} onChange={event => patch('note',event.target.value)}/></label>
        { [['multimodal','支持多模态'],['thinking_mode','启用思考模式'],['enabled','添加时启用部署'],['use_system_proxy','启用环境代理']].map(([key,label]) => <label className="preset-check" key={key}><input type="checkbox" checked={!!p[key]} onChange={event => patch(key,event.target.checked)}/>{label}</label> ) }
      </div></section>
    </div>
    {deleteTarget && <div className="preset-confirm-backdrop" role="presentation" onMouseDown={event => {if(event.target===event.currentTarget)setDeleteTarget(null)}}><section className="preset-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="preset-delete-title"><h2 id="preset-delete-title">确认删除供应商</h2><p>删除“{items[deleteTarget]?.name||deleteTarget}”后，该预设将从列表中移除。</p><p>请输入下方验证码以继续：</p><code>{deleteCode}</code><input autoFocus value={deleteInput} onChange={event => setDeleteInput(event.target.value)} onKeyDown={event => {if(event.key==='Enter')confirmDelete()}} aria-label="输入验证码" placeholder="输入 6 位验证码"/><div><button type="button" className="button secondary" onClick={() => setDeleteTarget(null)}>取消</button><button type="button" className="button danger" disabled={deleteInput.trim().toUpperCase()!==deleteCode} onClick={confirmDelete}>确认删除</button></div></section></div>}
  </>
}
