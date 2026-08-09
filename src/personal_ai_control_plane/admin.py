from fastapi.responses import HTMLResponse


def admin_page() -> HTMLResponse:
    return HTMLResponse(HTML)


HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personal AI Control Plane</title>
<style>
:root{font-family:Inter,Segoe UI,Arial,sans-serif;color:#111827;background:#f6f7f9}body{margin:0}.wrap{max-width:1180px;margin:auto;padding:26px}h1{margin:0;font-size:26px}h2{font-size:18px;margin:0 0 12px}h3{font-size:14px;margin:16px 0 8px}.muted{color:#6b7280;font-size:13px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row>*{flex:1;min-width:0}input,select,button,.button{font:inherit}input,select{box-sizing:border-box;width:100%;padding:9px 10px;border:1px solid #d1d5db;border-radius:8px}button,.button{box-sizing:border-box;padding:9px 12px;border:0;border-radius:8px;background:#111827;color:#fff;cursor:pointer;text-align:center;text-decoration:none}button.secondary,.button.secondary{background:#e5e7eb;color:#111827}button.danger{background:#991b1b}.item{border:1px solid #e5e7eb;border-radius:9px;padding:10px;margin:7px 0}.pill{display:inline-block;padding:2px 7px;border-radius:999px;background:#eef2ff;font-size:11px;margin-left:5px}.primary{background:#dcfce7}.mono{font-family:Consolas,monospace;font-size:12px;white-space:pre-wrap}.error,.ok{display:none;padding:10px;border-radius:8px;margin:10px 0;white-space:pre-wrap}.error{background:#fee2e2;color:#991b1b}.ok{background:#ecfdf5;color:#065f46}.top{display:flex;justify-content:space-between;gap:12px;align-items:center}.top>.row{flex-wrap:nowrap}.top>.row>*{flex:0 0 auto;white-space:nowrap}.caps{font-size:12px;background:#f3f4f6;padding:10px;border-radius:8px}.resource{background:#f9fafb}.warn{background:#fffbeb}@media(max-width:800px){.grid{grid-template-columns:1fr}}@media(max-width:520px){.row{display:grid;grid-template-columns:1fr;align-items:stretch}.top{align-items:stretch;flex-direction:column}.top>.row{grid-template-columns:repeat(2,minmax(0,1fr))}.top>.row>*{white-space:normal}}
</style></head><body><div class="wrap">
<div class="top"><div><h1>Personal AI Control Plane</h1><div class="muted">v0.4.0 · 只绑定、不复制</div></div><div class="row"><button class="secondary" onclick="loadAll()">刷新</button><a class="button secondary" href="/docs" target="_blank">API Docs</a></div></div>
<div class="caps">Multica 拥有 Project / Issue / Task / Agent / Project Resources；Open WebUI 拥有 Knowledge / Memory / Skills / Tools / Computer Workspace；Control Plane 只保存 Context 映射与路由。</div>
<div id="error" class="error"></div><div id="ok" class="ok"></div>

<div class="card"><h2>Open WebUI Agent Tool Server</h2>
<div class="muted">日常聊天只接只读 Agent API，不暴露 Context CRUD。Open WebUI 中配置 OpenAPI Tool Server：</div>
<div class="item mono">Server URL: http://127.0.0.1:8765
Spec Path:  openwebui/openapi.json
Recommended ID: aicp</div>
<div class="row"><a class="button secondary" href="/openwebui/openapi.json" target="_blank">查看 Agent OpenAPI</a><button class="secondary" onclick="navigator.clipboard.writeText('http://127.0.0.1:8765/openwebui/openapi.json');showOk('已复制 Spec URL')">复制 Spec URL</button></div>
</div>

<div class="card"><h2>创建 Context</h2><div class="grid"><input id="ctxId" placeholder="asset-center"><input id="ctxName" placeholder="Asset Center"><input id="ctxAliases" placeholder="别名，逗号分隔"></div><div style="margin-top:10px"><button onclick="createContext()">创建</button></div></div>

<div class="card"><h2>Multica 浏览</h2><div class="row"><button class="secondary" onclick="loadWorkspaces()">加载 Workspaces</button><select id="ws" onchange="loadProjects()"><option value="">先加载 Workspace</option></select><select id="project"><option value="">选择 Project</option></select></div><div class="muted" style="margin-top:8px">选中的 Workspace / Project 可以直接绑定到下面任意 Context。绑定后 Project Resources 会实时从 Multica 读取，不存到本地。</div></div>

<div id="contexts"></div>
</div>
<script>
const q=s=>document.querySelector(s); const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
function showErr(e){q('#error').style.display='block';q('#error').textContent=typeof e==='string'?e:JSON.stringify(e,null,2)}
function showOk(s){q('#ok').style.display='block';q('#ok').textContent=s;setTimeout(()=>q('#ok').style.display='none',1800)}
async function api(url,opt={}){let r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt});let t=await r.text();let d;try{d=t?JSON.parse(t):null}catch{d=t}if(!r.ok)throw d;return d}
function arr(raw, keys){if(Array.isArray(raw))return raw;if(raw&&typeof raw==='object'){for(const k of keys){if(Array.isArray(raw[k]))return raw[k]}}return []}
function idOf(x){return x?.id||x?.workspace_id||x?.workspaceId||x?.uuid||''}
function labelOf(x){return x?.slug||x?.title||x?.name||idOf(x)||'unknown'}
async function createContext(){try{await api('/contexts',{method:'POST',body:JSON.stringify({id:q('#ctxId').value.trim(),name:q('#ctxName').value.trim(),aliases:q('#ctxAliases').value.split(',').map(x=>x.trim()).filter(Boolean)})});showOk('已创建');loadAll()}catch(e){showErr(e)}}
async function editCtx(ctx){try{let c=await api(`/contexts/${ctx}`);let name=prompt('Context 名称',c.name);if(name===null)return;name=name.trim();if(!name)return showErr('Context 名称不能为空');let aliases=prompt('别名，逗号分隔',(c.aliases||[]).join(','));if(aliases===null)return;await api(`/contexts/${ctx}`,{method:'PATCH',body:JSON.stringify({name,aliases:aliases.split(',').map(x=>x.trim()).filter(Boolean)})});showOk('Context 已更新');loadAll()}catch(e){showErr(e)}}
async function loadWorkspaces(){try{let d=await api('/multica/workspaces');let xs=arr(d.workspaces,['workspaces','items','data','results']);q('#ws').innerHTML='<option value="">选择 Workspace</option>'+xs.map(x=>`<option value="${esc(idOf(x)||x.slug)}">${esc(labelOf(x))}</option>`).join('')}catch(e){showErr(e)}}
async function loadProjects(){let ws=q('#ws').value;if(!ws)return;try{let d=await api('/multica/projects?workspace='+encodeURIComponent(ws));let xs=arr(d.projects,['projects','items','data','results']);q('#project').innerHTML='<option value="">选择 Project</option>'+xs.map(x=>`<option value="${esc(x.id||x.project_id||x.uuid||'')}">${esc(x.title||x.name||x.id)}</option>`).join('')}catch(e){showErr(e)}}
async function bindMultica(ctx,role){let ws=q('#ws').value,project=q('#project').value;if(!ws||!project)return showErr('先选择 Workspace 和 Project');try{await api(`/contexts/${ctx}/multica-bindings`,{method:'POST',body:JSON.stringify({workspace:ws,project,role})});showOk('Multica Project 已绑定');loadAll()}catch(e){showErr(e)}}
async function bindKb(ctx){let kid=q(`#kb-${CSS.escape(ctx)}`).value.trim(),label=q(`#kbl-${CSS.escape(ctx)}`).value.trim();if(!kid)return showErr('请输入已有 Open WebUI Knowledge ID');try{await api(`/contexts/${ctx}/openwebui-knowledge`,{method:'POST',body:JSON.stringify({knowledge_id:kid,label:label||null,role:'related'})});showOk('Knowledge 引用已绑定');loadAll()}catch(e){showErr(e)}}
async function loadResources(ctx){let box=q(`#res-${CSS.escape(ctx)}`);box.textContent='加载中...';try{let d=await api(`/contexts/${ctx}/resources`);let html='';for(const b of d.bindings||[]){html+=`<div class="item resource"><b>${esc(b.workspace)} / ${esc(b.project)}</b> <span class="pill ${b.role==='primary'?'primary':''}">${esc(b.role)}</span>`;for(const r of b.resources||[]){if(r.resource_type==='local_directory')html+=`<div class="mono">local_directory · ${esc(r.local_path)} ${r.path_exists_on_control_plane_host?'✓ 本机可见':'· 本机不可见'}</div>`;else if(r.resource_type==='github_repo')html+=`<div class="mono">github_repo · ${esc(r.url)}${r.ref?' @ '+esc(r.ref):''}</div>`;else html+=`<div class="mono">${esc(r.resource_type)} · ${esc(JSON.stringify(r.resource_ref))}</div>`}html+='</div>'}if((d.errors||[]).length)html+=`<div class="item warn mono">${esc(JSON.stringify(d.errors,null,2))}</div>`;box.innerHTML=html||'<div class="muted">没有 Project Resources</div>'}catch(e){box.textContent='';showErr(e)}}
async function delCtx(ctx){if(!confirm('删除 Context 映射？不会删除 Multica/Open WebUI 里的任何数据。'))return;try{await api(`/contexts/${ctx}`,{method:'DELETE'});loadAll()}catch(e){showErr(e)}}
async function loadAll(){q('#error').style.display='none';try{let d=await api('/contexts');let html='';for(const c of d.contexts||[]){html+=`<div class="card"><div class="top"><div><h2>${esc(c.name)} <span class="pill">${esc(c.id)}</span></h2><div class="muted">Aliases: ${esc((c.aliases||[]).join(', ')||'-')}</div></div><div class="row"><button class="secondary" onclick="editCtx('${esc(c.id)}')">编辑 Context</button><button class="danger" onclick="delCtx('${esc(c.id)}')">删除 Context</button></div></div>`;
html+=`<h3>Multica Bindings</h3><div>${(c.multica_bindings||[]).map(b=>`<div class="item">${esc(b.workspace)} / ${esc(b.project)} <span class="pill ${b.role==='primary'?'primary':''}">${esc(b.role)}</span></div>`).join('')||'<div class="muted">未绑定</div>'}</div><div class="row"><button onclick="bindMultica('${esc(c.id)}','primary')">绑定当前 Project 为 PRIMARY</button><button class="secondary" onclick="bindMultica('${esc(c.id)}','related')">绑定为 RELATED</button><button class="secondary" onclick="loadResources('${esc(c.id)}')">读取 Project Resources</button></div><div id="res-${esc(c.id)}"></div>`;
html+=`<h3>Open WebUI Knowledge Bindings</h3><div>${(c.openwebui_knowledge_bindings||[]).map(k=>`<div class="item">${esc(k.label||k.knowledge_id)} <span class="pill">${esc(k.knowledge_id)}</span></div>`).join('')||'<div class="muted">未绑定。Knowledge 内容仍由 Open WebUI 管理和检索。</div>'}</div><div class="grid"><input id="kb-${esc(c.id)}" placeholder="Open WebUI Knowledge ID"><input id="kbl-${esc(c.id)}" placeholder="显示名称（可选）"><button onclick="bindKb('${esc(c.id)}')">绑定已有 Knowledge</button></div>`;
html+='</div>'}q('#contexts').innerHTML=html||'<div class="card muted">还没有 Context</div>'}catch(e){showErr(e)}}
loadAll();
</script></body></html>"""
