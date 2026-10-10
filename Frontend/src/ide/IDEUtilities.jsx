import * as monaco from "monaco-editor";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../services/api";

export function GlobalSearch({ workspace, files, onOpen, onClose, onFileSaved }) {
  const [query,setQuery]=useState("");
  const [replace,setReplace]=useState("");
  const [caseSensitive,setCaseSensitive]=useState(false);
  const [regex,setRegex]=useState(false);
  const [replaceMode,setReplaceMode]=useState(false);
  const escapeRegex=(value)=>String(value??"").split("").map(char=>"\\^$.*+?()[]{}|".includes(char)?"\\"+char:char).join("");
  const results=useMemo(()=>{if(!workspace||!query)return [];let re;try{re=regex?new RegExp(query,caseSensitive?"g":"gi"):new RegExp(escapeRegex(query),caseSensitive?"g":"gi")}catch{return []}const out=[];Object.entries(files||{}).forEach(([path,text])=>String(text).split("\n").forEach((line,i)=>{re.lastIndex=0;let m;while((m=re.exec(line))){out.push({path,line:i+1,text:line.trim(),column:m.index+1,match:m[0]});if(!re.global)break;if(m[0]==="")re.lastIndex++}}));return out.slice(0,500);  },[files,query,regex,caseSensitive,workspace]);
  async function applyReplace(all=false){if(!query||!results.length||!workspace)return;let rx;try{rx=regex?new RegExp(query,caseSensitive?"g":"gi"):new RegExp(escapeRegex(query),caseSensitive?"g":"gi")}catch(e){alert(e.message||"Invalid regular expression");return}const targets=all?[...new Set(results.map(r=>r.path))]:[results[0].path];if(all&&!window.confirm(`Replace ${results.length} matches in ${targets.length} files?`))return;let revision=Number(workspace.revision??0);const next={...files};try{for(const path of targets){const content=String(next[path]||"").replace(rx,replace);const resp=await apiFetch(`/ide/workspaces/${workspace.id}/files/`,{method:"POST",body:JSON.stringify({path,content,revision})});const d=await resp.json();if(!resp.ok)throw Error(d.error||d.detail||("Replace failed for "+path));next[path]=content;revision=Number(d.revision??revision);onFileSaved?.(path,content,revision)}setQuery("");}catch(e){alert(e.message||"Replace failed");}}
  return <div className="dos-search-panel"><div className="dos-search-head"><strong>SEARCH</strong><span className="dos-search-count">{results.length}</span><button onClick={onClose}>×</button></div><div className="dos-search-input"><span>⌕</span><input autoFocus value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search in workspace…"/><button className={caseSensitive?"on":""} onClick={()=>setCaseSensitive(x=>!x)}>Aa</button><button className={regex?"on":""} onClick={()=>setRegex(x=>!x)}>.*</button></div><div className="dos-search-tabs"><button className={!replaceMode?"active":""} onClick={()=>setReplaceMode(false)}>Find</button><button className={replaceMode?"active":""} onClick={()=>setReplaceMode(true)}>Replace</button></div>{replaceMode&&<div className="dos-search-input"><span>↪</span><input value={replace} onChange={e=>setReplace(e.target.value)} placeholder="Replace with…"/><button onClick={()=>applyReplace(false)}>Replace</button><button onClick={()=>applyReplace(true)}>All</button></div>}<div className="dos-search-meta">{results.length} result{results.length===1?"":"s"}</div><div className="dos-search-results">{results.map((r,i)=><button key={`${r.path}:${r.line}:${i}`} onClick={()=>onOpen(r.path,r.line,r.column)}><strong>{r.path}</strong><span><b>{r.line}</b> {r.text||"(empty line)"}</span></button>)}</div></div>
}

export function ProblemsPanel({ diagnostics=[], onClose, onOpen }) {
 return <section className="dos-problems"><header><strong>PROBLEMS</strong><span>{diagnostics.length}</span><button onClick={onClose}>×</button></header>{diagnostics.length?<div>{diagnostics.map((d,i)=><button key={i} onClick={()=>onOpen?.(d)}><i className={d.severity||"error"}/><span><b>{d.path||"Current file"}</b><small>Line {d.line||1}:{d.column||1} — {d.message}</small></span></button>)}</div>:<p>✓ No problems detected.</p>}</section>
}

function defaultRunCommand(path, files) {
 const p=String(path||"").replace(/\\\\/g,"/");
 const ext=p.split(".").pop()?.toLowerCase();
 if(!p || !Object.prototype.hasOwnProperty.call(files||{},p)) return "npm run build";
 if(ext==="py") return `python3 ${JSON.stringify(p)}`;
 if(ext==="js" || ext==="mjs" || ext==="cjs") return `node ${JSON.stringify(p)}`;
 if(ext==="ts") return `npx --no-install tsx ${JSON.stringify(p)}`;
 return "npm run build";
}

export function RunCenter({workspace,files,activeFile,onClose}) {
 const [command,setCommand]=useState(()=>defaultRunCommand(activeFile,files)),[running,setRunning]=useState(false),[output,setOutput]=useState(""),[exitCode,setExitCode]=useState(null),[duration,setDuration]=useState(0),[tab,setTab]=useState("terminal"),[processes,setProcesses]=useState([]),[busy,setBusy]=useState(false),[jobId,setJobId]=useState(null),[queueWait,setQueueWait]=useState(null),[queuePosition,setQueuePosition]=useState(null);
 const streamTimerRef=useRef(null);
 const presets=[["Build","npm run build"],["Test","npm test"],["Lint","npm run lint"],["Python","python3 main.py"],["Django","python3 manage.py check"]];
 async function execute(){
  if(!workspace||running||!command.trim())return;
  setJobId(null);setQueueWait(null);setQueuePosition(null);setRunning(true);setExitCode(null);setOutput("");setTab("terminal");
  const started=performance.now();let cursor=0;let failures=0;let stopped=false;
  try{
   const r=await apiFetch(`/ide/workspaces/${workspace.id}/execute/`,{method:"POST",body:JSON.stringify({command:command.trim(),active_file:activeFile||"",files:files||workspace.files||{}})});
   const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||"Secure runner unavailable");
   if(d.job_id){
    setJobId(d.job_id);setQueueWait(d.queue_wait_ms??null);setQueuePosition(d.queue_position??null);
    const initialOutput=[d.stdout,d.stderr].filter(Boolean).join("\n");
    setOutput(initialOutput || `Job ${d.job_id.slice(0,8)} · ${d.status||"queued"}`);
    // The runner may complete synchronously. In that case the response already
    // contains the authoritative output and there is no reason to wait for a
    // second status/stream cycle.
    if(["success","completed","failed","timeout","cancelled"].includes(d.status)){
      setExitCode(d.exit_code??(["success","completed"].includes(d.status)?0:-1));
      setDuration(d.duration_ms??Math.round(performance.now()-started));
      setRunning(false);setJobId(null);return;
    }
    const poll=async()=>{
     if(stopped)return;
     try{
      const s=await apiFetch(`/ide/workspaces/${workspace.id}/jobs/${encodeURIComponent(d.job_id)}/`);
      const j=await s.json();if(!s.ok)throw Error(j.error||j.detail||"Job status unavailable");
      if(typeof j.queue_wait_ms==="number")setQueueWait(j.queue_wait_ms); if(typeof j.queue_position==="number")setQueuePosition(j.queue_position);
      if(j.error)setOutput(prev=>prev.includes(j.error)?prev:prev+`\n${j.error}`);
      const o=await apiFetch(`/ide/workspaces/${workspace.id}/jobs/${encodeURIComponent(d.job_id)}/stream/?cursor=${cursor}`);
      const od=await o.json();if(!o.ok)throw Error(od.error||od.detail||"Live stream unavailable");
      if(od.output){setOutput(prev=>prev+od.output);cursor=typeof od.cursor==="number"?od.cursor:cursor}
      if(["success","completed","failed","timeout","cancelled"].includes(j.status)){
       stopped=true;streamTimerRef.current=null;setJobId(null);setQueueWait(j.queue_wait_ms??null);
       setExitCode(j.exit_code??(j.status==="completed"?0:-1));setDuration(j.duration_ms??Math.round(performance.now()-started));setRunning(false);return;
      }
      failures=0;streamTimerRef.current=setTimeout(poll,100);
     }catch(e){
      if(stopped)return;failures++;
      if(failures>=5){stopped=true;streamTimerRef.current=null;setOutput(prev=>prev+`\nStream disconnected: ${e.message||"temporary network error"}`);setExitCode(-1);setRunning(false);setJobId(null);return}
      streamTimerRef.current=setTimeout(poll,Math.min(2000,250*2**(failures-1)));
     }
    };poll();
   }else{
    setOutput([d.stdout,d.stderr].filter(Boolean).join("\n"));setExitCode(d.exit_code??null);setDuration(d.duration_ms||Math.round(performance.now()-started));setRunning(false);
   }
  }catch(e){setOutput(e.message||"Execution failed");setExitCode(-1);setRunning(false)}
 }
 async function cancelJob(){
  if(!workspace||!jobId)return;
  if(streamTimerRef.current){clearTimeout(streamTimerRef.current);streamTimerRef.current=null}
  try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/jobs/${encodeURIComponent(jobId)}/cancel/`,{method:"POST"});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||d.detail||"Job cancellation failed");setOutput(`Job ${jobId.slice(0,8)} · ${d.status||"cancelled"}`);setExitCode(-1);setRunning(false);setJobId(null)}catch(e){setOutput(e.message||"Job cancellation failed")}
 }
 async function refreshProcesses(){if(!workspace)return;setBusy(true);try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/processes/`);const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||`Process API unavailable (HTTP ${r.status})`);setProcesses(Array.isArray(d)?d:(d.processes||[]))}catch(e){setOutput(e.message||"Process API unavailable")}finally{setBusy(false)}}
 async function startProcess(){if(!workspace||busy||!command.trim())return;setBusy(true);setOutput("");try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/process/start/`,{method:"POST",body:JSON.stringify({command:command.trim(),active_file:activeFile||"",files:files||workspace.files||{}})});const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||`Process start failed (HTTP ${r.status})`);setOutput([d.stdout,d.stderr].filter(Boolean).join("\n")||`Process ${d.id||""} started.`);setTab("processes");await refreshProcesses()}catch(e){setOutput(e.message||"Process start failed")}finally{setBusy(false)}}
 async function stopProcess(id){if(!workspace||!id)return;setBusy(true);try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/process/${encodeURIComponent(id)}/`,{method:"POST",body:JSON.stringify({operation:"stop"})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||d.detail||`Process stop failed (HTTP ${r.status})`);await refreshProcesses()}catch(e){setOutput(e.message||"Process stop failed")}finally{setBusy(false)}}
 useEffect(()=>{setCommand(defaultRunCommand(activeFile,files));},[activeFile,workspace?.id]);
 useEffect(()=>{refreshProcesses();const t=setInterval(refreshProcesses,4000);return()=>{clearInterval(t);if(streamTimerRef.current)clearTimeout(streamTimerRef.current)}},[workspace?.id]);
 return <section className="dos-run-center"><header><div><strong>CODE EXECUTION</strong><small>SECURE SANDBOX · BUILD · TEST · DEBUG</small></div><button onClick={onClose}>×</button></header><div className="dos-run-presets">{presets.map(([name,cmd])=><button key={name} onClick={()=>setCommand(cmd)}>{name}</button>)}</div><div className="dos-run-command"><span>›</span><input value={command} onChange={e=>setCommand(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")execute()}}/><button className="dos-run-main" onClick={execute} disabled={running||busy}>{running?"■ Running…":"▶ Run"}</button>{running&&jobId?<button className="dos-run-cancel" onClick={cancelJob} disabled={busy}>✕ Cancel Job</button>:null}<button onClick={startProcess} disabled={running||busy||!command.trim()}>⚙ Start Process</button></div><div className="dos-run-tabs"><button className={tab==="terminal"?"active":""} onClick={()=>setTab("terminal")}>TERMINAL</button><button className={tab==="processes"?"active":""} onClick={()=>setTab("processes")}>PROCESSES {processes.length}</button><button onClick={refreshProcesses}>↻</button></div>{tab==="terminal"?<div className="dos-run-output"><div className="dos-run-metrics"><span>STATUS <b>{running?"RUNNING":exitCode===0?"SUCCESS":exitCode===null?"READY":"FAILED"}</b></span><span>EXIT <b>{exitCode??"—"}</b></span><span>QUEUE <b>{running&&queuePosition>0?"#"+queuePosition:"—"}</b></span><span>WAIT <b>{queueWait==null?"—":queueWait+"ms"}</b></span><span>TIME <b>{duration}ms</b></span><span>RUNTIME <b>ISOLATED</b></span></div><pre>{output||"Ready. Code executes in the configured Developer OS runner, not in the browser."}</pre></div>:<div className="dos-process-list">{processes.length?processes.map((p,i)=><div className="dos-process" key={p.id||p.process_id||i}><div><b>{p.command||p.name||"process"}</b><small>{p.id||p.process_id} · {p.status||"running"}</small></div><button onClick={()=>stopProcess(p.id||p.process_id)} disabled={busy}>Stop</button></div>):<div className="dos-empty">No active processes.</div>}</div>}<footer><span>🔒 Sandbox execution · resource limits enforced by runner</span><button onClick={()=>setTab("processes")}>Manage processes</button></footer></section>
}
export function DebuggerPanel({workspace,activeFile,onPrepare,onOpen,onClose}) {
 const [session,setSession]=useState(null),[state,setState]=useState("idle"),[breakpoints,setBreakpoints]=useState([]),[expression,setExpression]=useState(""),[result,setResult]=useState(null),[stack,setStack]=useState([]),[scopes,setScopes]=useState([]),[variables,setVariables]=useState([]),[output,setOutput]=useState(""),[busy,setBusy]=useState(false),[line,setLine]=useState(1),[polling,setPolling]=useState(false);
 async function call(action,extra={}){if(!workspace)return;setBusy(true);try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/debug/`,{method:"POST",body:JSON.stringify({action,session_id:session,path:activeFile,...extra})});const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||"Debugger request failed");setSession(d.session_id||session);setState(d.state||state);setStack(d.stack||[]);setScopes(d.scopes||[]);setVariables(d.variables||[]);setOutput(d.output||"");setResult(d.result??null);if(d.breakpoints)setBreakpoints(d.breakpoints);return d}catch(e){setOutput(e.message||"Debugger request failed")}finally{setBusy(false)}}
 useEffect(()=>{if(!session||!workspace)return;setPolling(true);const timer=setInterval(async()=>{try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/debug/`,{method:"POST",body:JSON.stringify({action:"status",session_id:session})});const d=await r.json();if(r.ok){setState(d.state||"idle");setStack(d.stack||[]);setScopes(d.scopes||[]);setVariables(d.variables||[]);setOutput(d.output||"");setResult(d.result??null);setBreakpoints(d.breakpoints||[]);}}catch(e){setOutput(e?.message||"Debugger status unavailable");}},1000);return()=>{clearInterval(timer);setPolling(false)}},[session,workspace?.id]);
 function toggleBreakpoint(){if(!activeFile)return;const existing=breakpoints.find(b=>b.path===activeFile&&Number(b.line)===Number(line));call(existing?"remove_breakpoint":"set_breakpoint",{path:activeFile,line:Number(line)});}
 return <section className="dos-debugger"><header><div><strong>DEBUGGER</strong><small>{state.toUpperCase()} · LIVE DAP</small></div><button onClick={onClose}>×</button></header><div className="dos-debug-toolbar"><button onClick={async()=>{await onPrepare?.();await call("start",{path:activeFile,line:Number(line)})}} disabled={busy||!activeFile}>▶ Start</button><button onClick={()=>call("continue")} disabled={!session||busy}>Continue</button><button onClick={()=>call("pause")} disabled={!session||busy}>Pause</button><button onClick={()=>call("step_over")} disabled={!session||busy}>Step Over</button><button onClick={()=>call("step_into")} disabled={!session||busy}>Step Into</button><button onClick={()=>call("step_out")} disabled={!session||busy}>Step Out</button><button onClick={()=>call("stop")} disabled={!session||busy}>■ Stop</button></div>
 <div className="dos-debug-breakpoint-bar"><span>Breakpoint line</span><input type="number" min="1" value={line} onChange={e=>setLine(Math.max(1,Number(e.target.value)||1))}/><button onClick={toggleBreakpoint} disabled={!activeFile||busy}>{breakpoints.some(b=>b.path===activeFile&&Number(b.line)===Number(line))?"Remove":"Set"} Breakpoint</button><span className="dos-debug-live">{polling?"● Live":"○ Idle"}</span></div>
 <div className="dos-debug-grid"><div><h4>CALL STACK</h4>{stack.length?stack.map((f,i)=><button key={i} onClick={()=>onOpen?.(f.source?.path||f.path,f.line,f.column)}><strong>{f.name||"frame"}</strong><small>{f.source?.path||f.path||"unknown"}:{f.line||1}</small></button>):<p>Start debugging to inspect the live stack.</p>}</div>
 <div><h4>SCOPES / VARIABLES</h4>{scopes.map((s,i)=><div key={i} className="dos-debug-scope"><b>{s.name}</b><small>{s.variablesReference?"variables available":"empty scope"}</small></div>)}{variables.length?variables.map((v,i)=><div className="dos-debug-var" key={i}><b>{v.name}</b><span>{String(v.value??"")}</span><small>{v.type||""}</small></div>):<p>No variables loaded for current frame.</p>}</div>
 <div><h4>BREAKPOINTS</h4>{breakpoints.length?breakpoints.map((b,i)=><button key={i} onClick={()=>onOpen?.(b.path,b.line,b.column)}><span>{b.verified?"●":"○"} {b.path}</span><small>line {b.line}{b.message?" · "+b.message:""}</small></button>):<p>No breakpoints.</p>}</div></div>
 <div className="dos-debug-console"><div><b>DEBUG CONSOLE</b><button onClick={()=>call("evaluate",{expression})} disabled={!session||busy||!expression.trim()}>Evaluate</button></div><input value={expression} onChange={e=>setExpression(e.target.value)} onKeyDown={e=>e.key==="Enter"&&call("evaluate",{expression})} placeholder="Evaluate expression in current frame…"/>{result!==null&&<pre>{typeof result==="string"?result:JSON.stringify(result,null,2)}</pre>}{output&&<pre>{output}</pre>}</div></section>
}
export function PreviewPanel({workspace,onClose}) {
 const [url,setUrl]=useState(""); const [loading,setLoading]=useState(false); const [error,setError]=useState("");
 async function start(){if(!workspace)return;setLoading(true);setError("");try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/preview/start/`,{method:"POST",body:JSON.stringify({framework:workspace.framework||""})});const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||`Preview start failed (HTTP ${r.status})`);setUrl(d.public_preview||d.preview_url||d.url||"")}catch(e){setError(e.message||"Preview could not be started")}finally{setLoading(false)}}
 return <div className="dos-preview"><header><strong>LIVE PREVIEW</strong><button onClick={onClose}>×</button></header><div className="dos-preview-toolbar"><button onClick={start}>{loading?"Starting…":"▶ Start Preview"}</button><input value={url} onChange={e=>setUrl(e.target.value)} placeholder="Preview path"/><button onClick={()=>url&&window.open(url,"_blank","noopener,noreferrer")}>↗</button></div>{error&&<div className="dos-preview-empty">{error}</div>}{url&&!error&&<div className="dos-preview-empty">Preview process started. Open the preview path in the authenticated Developer OS session.</div>}{!url&&!error&&<div className="dos-preview-empty">Start a workspace preview to launch the configured framework runner.</div>}</div>
}

function changedGitPaths(value){
 return String(value||"").split(/\r?\n/).map(line=>line.trimEnd()).filter(Boolean).map(line=>{
  const code=line.slice(0,2).trim()||"??";
  let path=line.length>3?line.slice(3).trim():line.trim();
  if(path.includes(" -> "))path=path.split(" -> ").pop().trim();
  path=path.replace(/^"(.*)"$/,"$1");
  return path?{path,code}:null;
 }).filter(Boolean);
}
function gitOutput(result){return String(result?.stdout??result?.output??"").trimEnd()}

export function SourceControlPanel({workspace,onClose,onSync}) {
 const [repos,setRepos]=useState([]),[name,setName]=useState(""),[selected,setSelected]=useState(""),[busy,setBusy]=useState(false),[message,setMessage]=useState("Connect a workspace to inspect its Git state."),[git,setGit]=useState({}),[commitMessage,setCommitMessage]=useState("Workspace update"),[branch,setBranch]=useState(""),[tab,setTab]=useState("changes"),[filter,setFilter]=useState("");
 const changedFiles=useMemo(()=>changedGitPaths(gitOutput(git.files)).filter(item=>item.path.toLowerCase().includes(filter.toLowerCase())),[git.files,filter]);
 const statusText=gitOutput(git.status);
 const diffText=gitOutput(git.diff);
 const branchText=gitOutput(git.branches);
 const historyText=gitOutput(git.log);
 const hasGitState=Boolean(git.status||git.files||git.diff||git.branches||git.log);
 const isError=/failed|error|unavailable|denied|not found|HTTP 5\d\d/i.test(message);
 useEffect(()=>{let active=true;apiFetch("/repositories/").then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||"Could not load repositories");return d}).then(d=>{if(active&&Array.isArray(d)){setRepos(d);if(d[0])setSelected(String(d[0].id));}}).catch(e=>{if(active)setMessage(e.message||"Could not load repositories")});return()=>{active=false}},[]);
 const refresh=useCallback(async function refreshRepositoryState(){
  if(!workspace?.id){setGit({});setMessage("Open a workspace to inspect repository state.");return}
  setBusy(true);
  const operations=[["status",["status","--short"]],["diff",["diff"]],["files",["status","--short"]],["branches",["branch","--list"]],["log",["log","-20","--oneline"]]];
  try{
   const results=await Promise.all(operations.map(async([operation,args])=>{
    const r=await apiFetch(`/ide/workspaces/${workspace.id}/git/`,{method:"POST",body:JSON.stringify({operation,args})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw Error(d.error||d.detail||`${operation} failed (HTTP ${r.status})`);
    return [operation,d];
   }));
   setGit(Object.fromEntries(results));
   setMessage("Git state synchronized.");
  }catch(e){setMessage(e.message||"Repository refresh failed")}
  finally{setBusy(false)}
 },[workspace?.id]);
 useEffect(()=>{void refresh()},[refresh]);
 async function gitAction(operation,args){
  if(!workspace?.id)return setMessage("Open a workspace first.");
  if(busy)return;
  setBusy(true);
  try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/git/`,{method:"POST",body:JSON.stringify({operation,args})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||d.detail||`${operation} failed (HTTP ${r.status})`);setGit(x=>({...x,[operation]:d}));setMessage(`${operation} completed.`);await refresh();}
  catch(e){setMessage(e.message||"Git operation failed")}
  finally{setBusy(false)}
 }
 async function gitFileAction(operation,path){
  if(!workspace?.id||busy||!path)return;
  if(operation==="discard"&&!window.confirm(`Discard changes for "${path}"? This cannot be undone.`))return;
  setBusy(true);
  try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/git/file/`,{method:"POST",body:JSON.stringify({operation,path})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||d.detail||`Git file operation failed (HTTP ${r.status})`);setMessage(`${operation} completed for ${path}`);await refresh();}
  catch(e){setMessage(e.message||"Git file operation failed")}
  finally{setBusy(false)}
 }
 async function openDiff(file,original,modified){
  try{
   const left=monaco.editor.createModel(original||"");
   const right=monaco.editor.createModel(modified||"");
   const container=document.createElement("div");
   container.className="dos-source-diff-modal";
   const header=document.createElement("div");header.className="dos-source-diff-head";
   const title=document.createElement("strong");title.textContent="DIFF · "+file;
   const close=document.createElement("button");close.type="button";close.textContent="×";close.setAttribute("aria-label","Close diff");
   const host=document.createElement("div");host.className="dos-source-diff-host";
   container.append(header,host);header.append(title,close);document.body.appendChild(container);
   const editor=monaco.editor.createDiffEditor(host,{readOnly:true,automaticLayout:true,renderSideBySide:true,theme:"vs-dark",minimap:{enabled:false},scrollBeyondLastLine:false});
   editor.setModel({original:left,modified:right});
   const cleanup=()=>{editor.dispose();left.dispose();right.dispose();container.remove()};
   close.onclick=cleanup;
   const onKey=e=>{if(e.key==="Escape"){cleanup();document.removeEventListener("keydown",onKey)}};
   document.addEventListener("keydown",onKey);
  }catch(e){setMessage(e.message||"Could not open diff viewer")}
 }
 async function create(){
  if(!name.trim()||busy)return;
  setBusy(true);
  try{const r=await apiFetch("/repositories/",{method:"POST",body:JSON.stringify({name:name.trim(),files:workspace?.files||{"README.md":"# Developer OS Repository\n"}})});const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||"Repository creation failed");setRepos(x=>[d,...x]);setSelected(String(d.id));setName("");setMessage("Independent repository created.");setTab("repositories");}
  catch(e){setMessage(e.message||"Repository creation failed")}
  finally{setBusy(false)}
 }
 async function nativeCommit(){
  const msg=commitMessage.trim();if(!msg)return setMessage("A commit message is required.");if(!workspace?.id)return setMessage("Open a workspace first.");if(busy)return;
  setBusy(true);
  try{
   const staged=await apiFetch(`/ide/workspaces/${workspace.id}/git/`,{method:"POST",body:JSON.stringify({operation:"commit",args:["add","-A"]})});const sd=await staged.json().catch(()=>({}));if(!staged.ok)throw Error(sd.error||sd.detail||"Staging changes failed");
   const committed=await apiFetch(`/ide/workspaces/${workspace.id}/git/`,{method:"POST",body:JSON.stringify({operation:"commit",args:["commit","-m",msg]})});const cd=await committed.json().catch(()=>({}));if(!committed.ok)throw Error(cd.error||cd.detail||"Commit failed");
   setCommitMessage("");setMessage("Commit created successfully.");await refresh();
  }catch(e){setMessage(e.message||"Commit failed")}
  finally{setBusy(false)}
 }
 async function nativeBranch(){
  const b=branch.trim();
  if(!workspace?.id)return setMessage("Open a workspace before creating a branch.");
  if(!b)return setMessage("Branch name is required.");
  if(!/^[A-Za-z0-9._/-]{1,120}$/.test(b)||b.startsWith("/")||b.endsWith("/")||b.includes("..")||b.includes("//")||b.endsWith(".")||b.includes("@{"))return setMessage("Invalid branch name.");
  if(busy)return;
  setBusy(true);
  try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/git/`,{method:"POST",body:JSON.stringify({operation:"branch",args:["checkout","-b",b]})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||d.detail||"Branch creation failed");setBranch("");setMessage("Created and switched to "+b);await refresh();}
  catch(e){setMessage(e.message||"Branch creation failed")}
  finally{setBusy(false)}
 }
 async function checkoutBranch(){
  const b=branch.trim();if(!workspace?.id)return setMessage("Open a workspace first.");if(!b)return setMessage("Enter a branch name.");if(busy)return;
  if(!window.confirm("Switch workspace to branch "+b+"?"))return;
  setBusy(true);
  try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/git/`,{method:"POST",body:JSON.stringify({operation:"checkout",args:["checkout",b]})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||d.detail||"Branch checkout failed");setBranch("");setMessage("Switched to "+b);await refresh();}
  catch(e){setMessage(e.message||"Branch checkout failed")}
  finally{setBusy(false)}
 }
 async function repositoryAction(type){
  if(!selected||!workspace?.id||busy)return;
  setBusy(true);
  try{const r=await apiFetch(`/repositories/${selected}/${type}/`,{method:"POST",body:JSON.stringify({workspace_id:workspace.id,message:commitMessage||"Workspace update"})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||d.detail||`${type} failed`);setMessage(type==="push"?"Committed to Developer OS Repository.":"Pulled from Developer OS Repository.");onSync?.(type==="push"?"push":"pull");}
  catch(e){setMessage(e.message||`${type} failed`)}
  finally{setBusy(false)}
 }
 const tabs=[["changes","Changes",changedFiles.length],["branches","Branches",null],["history","History",null],["repositories","Repositories",repos.length]];
 return <section className="dos-source-control dos-source-control-pro" role="dialog" aria-label="Source Control">
  <header className="dos-source-head">
   <div className="dos-source-brand"><div className="dos-source-mark">⑂</div><div><strong>Source Control</strong><small>WORKTREE <span>•</span> GIT INTEGRATION</small></div></div>
   <div className="dos-source-head-actions"><span className={`dos-source-connection ${workspace?.id?"connected":"disconnected"}`}><i/>{workspace?.id?"Workspace linked":"No workspace"}</span><button type="button" className="dos-source-icon-button" onClick={()=>void refresh()} disabled={busy} title="Refresh repository state" aria-label="Refresh repository state">↻</button><button type="button" className="dos-source-icon-button" onClick={onClose} title="Close Source Control" aria-label="Close Source Control">×</button></div>
  </header>
  <div className="dos-source-context"><span className="dos-source-context-icon">⌘</span><div><strong>{workspace?.name||"No workspace selected"}</strong><small>{workspace?.id? `Workspace #${workspace.id} · Native Git worktree`:"Select or open a workspace to enable Git actions."}</small></div><span className="dos-source-context-spacer"/><span className={`dos-source-sync-state ${busy?"is-busy":hasGitState?"is-ready":"is-idle"}`}><i/>{busy?"Syncing":hasGitState?"Synced":"Waiting"}</span></div>
  <div className="dos-source-commandbar">
   <div className="dos-source-branch-input"><span>⑂</span><input value={branch} onChange={e=>setBranch(e.target.value)} placeholder="Create or checkout branch…" aria-label="Branch name" onKeyDown={e=>{if(e.key==="Enter")void nativeBranch()}}/><button type="button" onClick={()=>void nativeBranch()} disabled={busy||!branch.trim()} title="Create and switch to branch">＋ Create</button><button type="button" onClick={()=>void checkoutBranch()} disabled={busy||!branch.trim()} title="Checkout existing branch">Checkout</button></div>
   <div className="dos-source-commit-input"><input value={commitMessage} onChange={e=>setCommitMessage(e.target.value)} placeholder="Describe the changes for your commit…" aria-label="Commit message" onKeyDown={e=>{if((e.ctrlKey||e.metaKey)&&e.key==="Enter")void nativeCommit()}}/><button type="button" className="dos-source-commit-button" onClick={()=>void nativeCommit()} disabled={busy||!workspace?.id||!commitMessage.trim()} title="Commit all worktree changes">✓ Commit</button></div>
  </div>
  <nav className="dos-source-tabs" aria-label="Source control views">{tabs.map(([key,label,count])=><button key={key} type="button" className={tab===key?"active":""} onClick={()=>setTab(key)} aria-current={tab===key?"page":undefined}><span>{label}</span>{count!==null&&<b>{count}</b>}</button>)}<span className="dos-source-tabs-spacer"/><button type="button" className="dos-source-refresh-tab" onClick={()=>void refresh()} disabled={busy}>↻ Refresh</button></nav>
  <div className="dos-source-main">
   <div className="dos-source-feedback" role="status" aria-live="polite"><span className={`dos-source-feedback-icon ${isError?"error":busy?"busy":"ok"}`}>{isError?"!":busy?"…":"✓"}</span><span>{message}</span></div>
   {tab==="changes"&&<>
    <div className="dos-source-view-head"><div><h3>Working tree</h3><p>Review, stage, compare, and commit workspace changes.</p></div><div className="dos-source-view-actions"><span className="dos-source-count-pill">{changedFiles.length} changed</span><button type="button" onClick={()=>{if(window.confirm("Restore the entire worktree and discard ALL unstaged changes? This cannot be undone."))void gitAction("restore",["restore","."])}} disabled={busy||!workspace?.id} className="dos-source-danger">Discard all</button></div></div>
    <div className="dos-source-file-toolbar"><label className="dos-source-filter"><span>⌕</span><input value={filter} onChange={e=>setFilter(e.target.value)} placeholder="Filter changed files…" aria-label="Filter changed files"/></label><span className="dos-source-muted">{changedFiles.length?"Select an action on any file":"No matching files"}</span></div>
    <div className="dos-source-change-list">
     {changedFiles.map(({path,code})=><article key={path} className="dos-source-change-row"><span className={`dos-source-file-status ${code.includes("?")?"untracked":code.includes("A")?"added":code.includes("D")?"deleted":code.includes("R")?"renamed":"modified"}`}>{code}</span><div className="dos-source-file-details"><code title={path}>{path.split("/").pop()}</code><small title={path}>{path.includes("/")?path.slice(0,path.lastIndexOf("/")):"Workspace root"}</small></div><div className="dos-source-file-actions"><button type="button" onClick={()=>void gitFileAction("stage",path)} disabled={busy} title="Stage file">＋ Stage</button><button type="button" onClick={async()=>{try{const r=await apiFetch(`/ide/workspaces/${workspace.id}/git/file/`,{method:"POST",body:JSON.stringify({operation:"diff",path})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||d.detail||"File diff unavailable");openDiff(path,d.original||d.base||"",d.stdout||d.output||"")}catch(e){setMessage(e.message||"File diff unavailable")}} disabled={busy} title="Open file diff">Diff ↗</button><button type="button" className="dos-source-discard-file" onClick={()=>void gitFileAction("discard",path)} disabled={busy} title="Discard file changes">Discard</button></div></article>)}
     {!changedFiles.length&&<div className="dos-source-empty"><div className="dos-source-empty-mark">{busy?"↻":"✓"}</div><strong>{busy?"Refreshing worktree…":filter?"No files match this filter":hasGitState?"Working tree clean":"Waiting for Git status"}</strong><p>{busy?"Fetching status, diffs, branches, and recent commits.":filter?"Try another filename or clear the filter.":hasGitState?"Your workspace has no uncommitted changes.":"Open a workspace with a Git worktree, then refresh to load repository state."}</p>{!busy&&<button type="button" onClick={()=>void refresh()} disabled={!workspace?.id}>Refresh status</button>}</div>}
    </div>
    <details className="dos-source-raw"><summary>Git status output</summary><pre>{statusText||"No status output returned."}</pre></details>
    <details className="dos-source-raw"><summary>Unified diff</summary><pre>{diffText||"No diff output returned."}</pre><button type="button" onClick={()=>openDiff("workspace diff","",diffText)} disabled={!diffText}>Open in Monaco Diff Editor</button></details>
   </>}{tab==="branches"&&<>
    <div className="dos-source-view-head"><div><h3>Branches</h3><p>Create an isolated line of work or switch to an existing branch.</p></div><span className="dos-source-count-pill">{branchText.split("\n").filter(Boolean).length} listed</span></div>
    <div className="dos-source-branch-list">{branchText?branchText.split("\n").filter(Boolean).map((line,i)=><div className="dos-source-branch-row" key={line+i}><span>⑂</span><code>{line.replace(/^\*\s*/,"")}</code>{line.startsWith("*")&&<b>Current</b>}<button type="button" onClick={()=>setBranch(line.replace(/^\*\s*/,"").trim())}>Select</button></div>):<div className="dos-source-empty"><div className="dos-source-empty-mark">⑂</div><strong>No branch data yet</strong><p>Refresh repository state to load local branches.</p></div>}</div>
   </>}{tab==="history"&&<>
    <div className="dos-source-view-head"><div><h3>Commit history</h3><p>Recent commits from this workspace repository.</p></div><span className="dos-source-count-pill">{historyText.split("\n").filter(Boolean).length} commits</span></div>
    <div className="dos-source-history-list">{historyText?historyText.split("\n").filter(Boolean).map((line,i)=>{const match=line.match(/^([0-9a-f]{7,40})\s*(.*)$/i);return <article className="dos-source-history-row" key={line+i}><span className="dos-source-history-node"/><div><code>{match?.[1]||"commit"}</code><p>{match?.[2]||line}</p></div><button type="button" onClick={()=>GenUI.copy(line)} title="Copy commit details">Copy</button></article>}):<div className="dos-source-empty"><div className="dos-source-empty-mark">◷</div><strong>No commit history loaded</strong><p>Refresh to retrieve recent commits from Git.</p></div>}</div>
   </>}{tab==="repositories"&&<>
    <div className="dos-source-view-head"><div><h3>Developer OS repositories</h3><p>Manage app-level repository snapshots separately from native Git.</p></div></div>
    <div className="dos-source-create-repository"><input value={name} onChange={e=>setName(e.target.value)} placeholder="Repository name…" aria-label="New repository name"/><button type="button" onClick={()=>void create()} disabled={busy||!name.trim()}>＋ Create repository</button></div>
    <label className="dos-source-repository-select">Repository<select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">Select repository</option>{repos.map(repo=><option key={repo.id} value={repo.id}>{repo.name} · {repo.branch}</option>)}</select></label>
    <div className="dos-source-repository-actions"><button type="button" onClick={()=>void repositoryAction("push")} disabled={busy||!selected||!workspace?.id}>↑ Push workspace snapshot</button><button type="button" onClick={()=>void repositoryAction("pull")} disabled={busy||!selected||!workspace?.id}>↓ Pull repository snapshot</button></div>
    <p className="dos-source-repository-note">These snapshot operations are separate from native Git commits and branches.</p>
    {repos.length===0&&<div className="dos-source-empty"><div className="dos-source-empty-mark">▤</div><strong>No Developer OS repositories</strong><p>Create a repository to use the app-level snapshot workflow.</p></div>}
   </>}
  </div>
 </section>
}
