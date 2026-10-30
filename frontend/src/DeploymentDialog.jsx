import React, { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Bot, Check, ChevronDown, ChevronRight, Eye, EyeOff, LoaderCircle, RefreshCw, Settings2, X, Zap } from 'lucide-react'

const services = presets => Object.entries(presets || {}).map(([id,p]) => ({id,name:p.name||id,mark:p.avatar_url ? <img className="provider-mark-image" src={p.avatar_url} alt="" onError={e=>{e.currentTarget.style.display='none';e.currentTarget.parentElement.textContent=p.avatar||p.name?.slice(0,2)||id.slice(0,2)}}/> : <>{p.avatar||p.name?.slice(0,2)||id.slice(0,2)}{p.name&&<img className="provider-mark-image provider-mark-overlay" src={`/images/providers/${p.name.trim().toLowerCase().replace(/\s+/g,'-')}.webp`} alt="" onError={e=>e.currentTarget.remove()}/>}</>,base:p.base_url,preset:p}))
const weightPresets=[['低权重',0.5],['无偏好',1],['高权重',2]]

export default function DeploymentDialog({editing,form,setForm,emptyForm,keyText,setKeyText,availableModels,setAvailableModels,deps,providerPresets={},busy,testingConnection,fetchingModels,onSave,onTest,onFetch,onClose}) {
  const [step,setStep] = useState(editing?'configure':'choose')
  const [serviceId,setServiceId] = useState(form.supplier_id||'custom')
  const [reveal,setReveal] = useState(false)
  const dialog = useRef(null)
  const closeState = useRef({working:false,onClose})
  const providerList=services(providerPresets)
  const service = providerList.find(s=>s.id===serviceId)
  const working = busy||testingConnection||fetchingModels
  closeState.current={working,onClose}
  const change = (key,value)=>setForm(old=>({...old,[key]:value}))
  const choose = id => {
    const preset=providerList.find(s=>s.id===id)?.preset
    setServiceId(id); setForm({...emptyForm,...preset,supplier_id:preset?id:'',supplier_name:preset?.name||'',full_url:false,upstream_model:'',name:'',alias:'',api_keys:[]})
    setKeyText('');setAvailableModels([]);setReveal(false);setStep('configure')
  }
  const models=[...new Set([...availableModels,...deps.filter(d=>d.base_url.replace(/\/$/,'')===form.base_url.replace(/\/$/,'')).map(d=>d.upstream_model)])]
  useEffect(()=>{
    const previous=document.activeElement
    dialog.current?.focus()
    const keydown=e=>{
      if(e.key==='Escape'&&!closeState.current.working){e.preventDefault();closeState.current.onClose()}
      if(e.key==='Tab'){
        const items=[...dialog.current.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary')].filter(el=>el.getClientRects().length)
        const first=items[0],last=items[items.length-1]
        if(e.shiftKey&&(document.activeElement===first||document.activeElement===dialog.current)){e.preventDefault();last?.focus()}
        else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}
      }
    }
    document.addEventListener('keydown',keydown)
    return ()=>{document.removeEventListener('keydown',keydown);previous?.focus()}
  },[])
  return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget&&!working)onClose()}}>
    <form ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="deployment-title" className="modal model-setup-modal" onSubmit={onSave}>
      <div className="modal-header"><div className="model-setup-title">{step==='configure'&&!editing&&<button type="button" className="icon-button" aria-label="返回服务商选择" disabled={working} onClick={()=>setStep('choose')}><ArrowLeft size={18}/></button>}<h2 id="deployment-title">{step==='choose'?'添加模型':editing?'编辑模型部署':service?'通过服务商添加':'自定义模型'}</h2></div><button type="button" className="icon-button" disabled={working} onClick={onClose} aria-label="关闭"><X size={18}/></button></div>
      {step==='choose'?<div className="modal-body"><p className="model-setup-intro">选择模型服务商，或配置自定义的兼容接口。</p><button type="button" className="provider-option provider-custom" onClick={()=>choose('custom')}><span className="provider-mark"><Bot size={19}/></span><span><b>自定义模型</b><small>OpenAI / Anthropic 兼容接口</small></span><ChevronRight size={16}/></button><div className="provider-options">{providerList.map(s=><button type="button" key={s.id} className={`provider-option provider-${s.id}`} disabled={!s.base} onClick={()=>choose(s.id)}><span className="provider-mark">{s.mark}</span><b>{s.name}</b>{s.base?<ChevronRight size={16}/>:<small>即将支持</small>}</button>)}</div></div>:<>
        <div className="modal-body model-setup-body">
          {service&&<label className="setup-field">服务商<select value={serviceId} disabled={working} onChange={e=>choose(e.target.value)}>{providerList.filter(s=>s.base).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>}
          {!service&&<><label className="setup-field">API 格式<select value={form.provider} onChange={e=>change('provider',e.target.value)}><option value="openai">OpenAI Chat Completions</option><option value="anthropic">Anthropic Messages</option></select></label><label className="setup-field"><span className="setup-url-label"><b>请求地址（Base URL）</b><span className="setup-url-mode"><span>完整 URL</span><span className="setup-switch"><input type="checkbox" aria-label="完整 URL" checked={!!form.full_url} onChange={e=>change('full_url',e.target.checked)}/><i/></span></span></span><input required type="url" value={form.base_url} onChange={e=>change('base_url',e.target.value)} placeholder={form.full_url?'https://api.example.com/v1/chat/completions':'https://api.example.com/v1'}/></label></>}
          <label className="setup-field"><span className="setup-model-label"><b>{service?'模型':'模型 ID'}</b>{service&&<button type="button" className="button secondary small" disabled={working} onClick={onFetch}>{fetchingModels?<LoaderCircle className="spin" size={14}/>:<RefreshCw size={14}/>}获取可用模型</button>}</span><input required maxLength={200} list="setup-models" value={form.upstream_model} onChange={e=>change('upstream_model',e.target.value)} placeholder={service?'选择或输入模型 ID':'输入模型 ID'}/><datalist id="setup-models">{models.map(m=><option key={m} value={m}/>)}</datalist>{service&&<small>支持手动输入，也可填写密钥后获取账号可用模型。{availableModels.length>0&&` 已获取 ${availableModels.length} 个模型。`}</small>}</label>
          <label className="setup-field">模型展示名称<input maxLength={100} value={form.name} onChange={e=>change('name',e.target.value)} placeholder="留空时使用模型 ID"/></label>
          <div className="setup-field"><div className="setup-key-label"><label htmlFor="setup-api-key">API 密钥</label>{service?.keys&&<a href={service.keys} target="_blank" rel="noreferrer">获取 API 密钥 ↗</a>}</div><div className="setup-key-input"><textarea id="setup-api-key" required={!editing} rows={2} className={reveal?'':'masked-key'} autoComplete="off" spellCheck={false} value={keyText} onChange={e=>setKeyText(e.target.value)} placeholder={editing?'留空保留已保存的密钥；填写则替换，每行一个':'请输入 API Key，多个密钥每行一个'}/><button type="button" className="icon-button" aria-label={reveal?'隐藏密钥':'显示密钥'} onClick={()=>setReveal(v=>!v)}>{reveal?<EyeOff size={16}/>:<Eye size={16}/>}</button></div></div>
          <details className="setup-advanced"><summary><Settings2 size={15}/>路由与限额<ChevronDown size={15}/></summary><div className="setup-advanced-grid">
            <label className="setup-field setup-wide">逻辑模型别名<input maxLength={100} value={form.alias} onChange={e=>change('alias',e.target.value)} placeholder="留空时使用模型 ID"/><small>客户端使用此名称请求；多个部署使用相同别名可参与路由。</small></label>
            {service&&<><label className="setup-field setup-wide">API Base URL<input required type="url" value={form.base_url} onChange={e=>change('base_url',e.target.value)}/></label><label className="setup-check setup-wide"><input type="checkbox" checked={!!form.full_url} onChange={e=>change('full_url',e.target.checked)}/>完整 URL（直接使用此地址，不拼接请求路径）</label></>}
            <label className="setup-field">RPM 参考值<input type="number" min="0" value={form.rpm} onChange={e=>change('rpm',+e.target.value)}/><small>0 表示未设置</small></label>
            <label className="setup-field"><span className="setup-preset-heading"><b>基础权重</b><span className="setup-preset-links">{weightPresets.map(([label,value])=><button key={label} type="button" onClick={()=>change('base_weight',value)}>{label}</button>)}</span></span><input type="number" min="0" max="100" step="0.1" value={form.base_weight??1} onChange={e=>change('base_weight',+e.target.value)}/><small>同一逻辑模型中的相对路由权重</small></label>
            <label className="setup-field"><span className="setup-preset-heading"><b>最大输入 Token</b><span className="setup-preset-links">{[128000,256000,512000,1000000].map(value=><button key={value} type="button" onClick={()=>change('max_input_tokens',value)}>{value>=1000000?`${value/1000000}M`:`${value/1000}k`}</button>)}</span></span><input type="number" min="0" value={form.max_input_tokens} onChange={e=>change('max_input_tokens',+e.target.value)}/><small>0 表示未设置</small></label>
            <label className="setup-field"><span className="setup-preset-heading"><b>最大输出 Token</b><span className="setup-preset-links">{[4000,16000,32000,128000].map(value=><button key={value} type="button" onClick={()=>change('max_output_tokens',value)}>{value/1000}k</button>)}</span></span><input type="number" min="0" value={form.max_output_tokens} onChange={e=>change('max_output_tokens',+e.target.value)}/><small>0 表示未设置</small></label>
            <label className="setup-field">请求超时（秒）<input type="number" min="1" max="600" value={form.timeout_seconds} onChange={e=>change('timeout_seconds',+e.target.value)}/></label>
            <label className="setup-field">自定义代理 URL<input value={form.proxy_url} onChange={e=>change('proxy_url',e.target.value)} placeholder="留空使用系统代理设置"/></label>
            <label className="setup-field setup-wide">备注<textarea rows={2} value={form.note} onChange={e=>change('note',e.target.value)} placeholder="可选"/></label>
            <div className="setup-check-grid setup-wide">
              <label className="setup-check"><input type="checkbox" checked={form.enabled} onChange={e=>change('enabled',e.target.checked)}/>启用此部署</label>
              <label className="setup-check"><input type="checkbox" checked={!!form.thinking_mode} onChange={e=>change('thinking_mode',e.target.checked)}/>启用思考模式</label>
              <label className="setup-check"><input type="checkbox" checked={form.use_system_proxy} onChange={e=>change('use_system_proxy',e.target.checked)}/>遵循系统 / 环境代理</label>
              <label className="setup-check"><input type="checkbox" checked={form.weight_locked} onChange={e=>change('weight_locked',e.target.checked)}/>锁定手动权重</label>
            </div>
          </div></details>
        </div><div className="modal-footer"><button type="button" className="button secondary" disabled={working} onClick={onTest}>{testingConnection?<LoaderCircle className="spin" size={14}/>:<Zap size={14}/>}测试连接</button><span className="modal-footer-spacer"/><button type="button" className="button secondary" disabled={working} onClick={()=>{if(editing){setForm({...editing,api_keys:[]});setKeyText('');setAvailableModels([])}else choose(serviceId)}}>重置</button><button className="button primary" disabled={working}>{busy?<LoaderCircle className="spin" size={14}/>:<Check size={14}/>} {editing?'保存部署':'添加模型'}</button></div>
      </>}
    </form>
  </div>
}

