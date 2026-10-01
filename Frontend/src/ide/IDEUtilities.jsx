import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../services/api";

export function GlobalSearch({ workspace, files, onOpen, onClose }) {
  const [query,setQuery]=useState("");
  const [replace,setReplace]=useState("");
  const [caseSensitive,setCaseSensitive]=useState(false);
  const [regex,setRegex]=useState(false);
  const [replaceMode,setReplaceMode]=useState(false);
  const results=useMemo(()=>{
    if(!workspace||!query)return [];
    let re;
    try { re=regex?new RegExp(query,caseSensitive?"g":"gi"):new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"),caseSensitive?"g":"gi"); } catch{return []}
    const out=[];
    Object.entries(files||{}).forEach(([path,text])=>String(text).split("\n").forEach((line,i)=>{if(re.test(line)){out.push({path,line:i+1,text:line.trim(),column:Math.max(1,line.search(re)+1)});re.lastIndex=0}}));
    return out.slice(0,250);
  },[files,query,regex,caseSensitive,workspace]);
  function applyReplace(all=false){if(!query||!results.length)return;const source=all?results:[results[0]];source.forEach(r=>{const text=files[r.path]||"";const lines=text.split("\n");let rx;try{rx=regex?new RegExp(query,caseSensitive?"g":"gi"):new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"),caseSensitive?"g":"gi")}catch{return}lines[r.line-1]=lines[r.line-1].replace(rx,replace);apiFetch(`/ide/workspaces/${workspace.id}/files/`,{method:"POST",body:JSON.stringify({path:r.path,content:lines.join("\n"),revision:workspace.revision})}).catch(()=>{})}); }
  return <div className="dos-search-panel"><div className="dos-search-head"><strong>SEARCH</strong><button onClick={onClose}>×</button></div><div className="dos-search-input"><span>⌕</span><input autoFocus value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search in workspace…"/><button className={caseSensitive?"on":""} onClick={()=>setCaseSensitive(x=>!x)}>Aa</button><button className={regex?"on":""} onClick={()=>setRegex(x=>!x)}>.*</button></div><div className="dos-search-tabs"><button className={!replaceMode?"active":""} onClick={()=>setReplaceMode(false)}>Find</button><button className={replaceMode?"active":""} onClick={()=>setReplaceMode(true)}>Replace</button></div>{replaceMode&&<div className="dos-search-input"><span>↪</span><input value={replace} onChange={e=>setReplace(e.target.value)} placeholder="Replace with…"/><button onClick={()=>applyReplace(false)}>Replace</button><button onClick={()=>applyReplace(true)}>All</button></div>}<div className="dos-search-meta">{results.length} result{results.length===1?"":"s"}</div><div className="dos-search-results">{results.map((r,i)=><button key={`${r.path}:${r.line}:${i}`} onClick={()=>onOpen(r.path,r.line,r.column)}><strong>{r.path}</strong><span><b>{r.line}</b> {r.text||"(empty line)"}</span></button>)}</div></div>
}

export function ProblemsPanel({ diagnostics=[], onClose, onOpen }) {
 return <section className="dos-problems"><header><strong>PROBLEMS</strong><span>{diagnostics.length}</span><button onClick={onClose}>×</button></header>{diagnostics.length?<div>{diagnostics.map((d,i)=><button key={i} onClick={()=>onOpen?.(d)}><i className={d.severity||"error"}/><span><b>{d.path||"Current file"}</b><small>Line {d.line||1}:{d.column||1} — {d.message}</small></span></button>)}</div>:<p>✓ No problems detected.</p>}</section>
}

export function RunCenter({workspace,onClose}) {
 const [command,setCommand]=useState("npm run dev");const [running,setRunning]=useState(false);const [output,setOutput]=useState("");
 async function execute(){if(!workspace||running)return;setRunning(true);setOutput("Starting process…");try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/execute/`,{method:"POST",body:JSON.stringify({command})});const d=await r.json();setOutput(`${d.stdout||""}${d.stderr?`\n${d.stderr}`:""}\n\nExit ${d.exit_code??0} · ${d.duration_ms||0}ms`)}catch(e){setOutput(e.message)}finally{setRunning(false)}}
 return <div className="dos-run-center"><header><strong>RUN & BUILD</strong><button onClick={onClose}>×</button></header><div className="dos-run-row"><select value={command} onChange={e=>setCommand(e.target.value)}><option>npm run dev</option><option>npm run build</option><option>npm test</option><option>python manage.py runserver</option></select><button onClick={execute}>{running?"Running…":"▶ Run"}</button></div><pre>{output||"Ready. Choose a command and run it in the workspace sandbox."}</pre></div>
}

export function PreviewPanel({workspace,onClose}) {
 const [url,setUrl]=useState(""); const [loading,setLoading]=useState(false);
 async function start(){if(!workspace)return;setLoading(true);try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/execute/`,{method:"POST",body:JSON.stringify({command:"npm run dev -- --host 0.0.0.0"})});const d=await r.json();setUrl(d.preview_url||d.url||"")}catch{}finally{setLoading(false)}}
 return <div className="dos-preview"><header><strong>LIVE PREVIEW</strong><button onClick={onClose}>×</button></header><div className="dos-preview-toolbar"><button onClick={start}>{loading?"Starting…":"▶ Start Preview"}</button><input value={url} onChange={e=>setUrl(e.target.value)} placeholder="Preview URL"/><button onClick={()=>url&&window.open(url,"_blank","noopener,noreferrer")}>↗</button></div>{url?<iframe title="Developer OS Preview" src={url}/>:<div className="dos-preview-empty">Start a workspace preview to see your application here.</div>}</div>
}


export function SourceControlPanel({workspace,onClose,onSync}) {
 const [repos,setRepos]=useState([]); const [name,setName]=useState(""); const [selected,setSelected]=useState(""); const [busy,setBusy]=useState(false); const [message,setMessage]=useState("Developer OS Repository — provider-free source control.");
 useEffect(()=>{apiFetch("/repositories/").then(r=>r.json()).then(d=>{if(Array.isArray(d)){setRepos(d);if(d[0])setSelected(String(d[0].id));}}).catch(e=>setMessage(e.message))},[]);
 async function create(){if(!name.trim()||busy)return;setBusy(true);try{const r=await apiFetch("/repositories/",{method:"POST",body:JSON.stringify({name:name.trim(),files:workspace?.files||{"README.md":"# Developer OS Repository\n"}})});const d=await r.json();if(!r.ok)throw Error(d.error||"Repository creation failed");setRepos(x=>[d,...x]);setSelected(String(d.id));setName("");setMessage("Independent repository created.");}catch(e){setMessage(e.message)}finally{setBusy(false)}}
 async function action(type){if(!selected||!workspace||busy)return;setBusy(true);try{const r=await apiFetch(`/repositories/${selected}/${type}/`,{method:"POST",body:JSON.stringify({workspace_id:workspace.id,message:"Workspace update"})});const d=await r.json();if(!r.ok)throw Error(d.error||`${type} failed`);setMessage(type==="push"?"Committed to Developer OS Repository.":"Pulled from Developer OS Repository.");onSync?.(type==="push"?"push":"pull");}catch(e){setMessage(e.message)}finally{setBusy(false)}}
 return <section className="dos-source-control"><header><div><strong>SOURCE CONTROL</strong><small>DEVELOPER OS NATIVE REPOSITORY</small></div><button onClick={onClose}>×</button></header><div className="dos-source-body"><label>Repository<select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">Select repository</option>{repos.map(r=><option key={r.id} value={r.id}>{r.name} · {r.branch}</option>)}</select></label><div className="dos-source-create"><input value={name} onChange={e=>setName(e.target.value)} placeholder="New repository name"/><button onClick={create} disabled={busy}>＋ Create</button></div><div className="dos-source-actions"><button onClick={()=>action("push")} disabled={!selected||busy}>↑ Commit / Push</button><button onClick={()=>action("pull")} disabled={!selected||busy}>↓ Pull</button></div><p>{message}</p><div className="dos-source-note">No GitHub account. No external remote. Your repository is stored and versioned inside Developer OS.</div></div></section>
}
