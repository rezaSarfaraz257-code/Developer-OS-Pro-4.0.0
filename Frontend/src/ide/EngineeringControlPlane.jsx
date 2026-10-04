import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../services/api";

const MANAGERS=[
  ["pnpm","pnpm-lock.yaml"],["yarn","yarn.lock"],["npm","package-lock.json"],["uv","uv.lock"],
  ["poetry","poetry.lock"],["pip","requirements.txt"],["cargo","Cargo.toml"],["go","go.mod"],
  ["maven","pom.xml"],["gradle","build.gradle"],["composer","composer.json"],["bundler","Gemfile"],
  ["pub","pubspec.yaml"],["swiftpm","Package.swift"],["mix","mix.exs"],["rebar3","rebar.config"],["dotnet",".csproj"],
];

function detect(files){
  const names=new Set(Object.keys(files||{}).map(p=>p.split("/").pop()));
  for(const [id,lock] of MANAGERS) if(names.has(lock)||Object.keys(files||{}).some(p=>p.endsWith(lock))) return {id,lockfile:names.has(lock)};
  if(names.has("package.json")) return {id:"npm",lockfile:false};
  if(names.has("pyproject.toml")) return {id:"uv",lockfile:false};
  return null;
}

export default function EngineeringControlPlane({workspace,files,onClose,onStatus}){
  const [caps,setCaps]=useState(null),[busy,setBusy]=useState(false),[output,setOutput]=useState("Control plane ready."),[action,setAction]=useState("install"),[packageName,setPackageName]=useState(""),[buildBusy,setBuildBusy]=useState(false),[tests,setTests]=useState([]),[testBusy,setTestBusy]=useState(false);
  const manager=useMemo(()=>detect(files),[files]);
  useEffect(()=>{apiFetch("/ide/capabilities/").then(r=>r.json()).then(setCaps).catch(()=>{});},[]);
  async function build(){
    if(!workspace||busy)return;
    setBusy(true); setOutput("Analyzing project → compiling build plan…");
    try{
      const r=await apiFetch("/ide/workspaces/"+workspace.id+"/build/plan/",{method:"POST",body:JSON.stringify({files:files||{}})});
      const d=await r.json();
      if(!r.ok)throw Error(d.error||d.detail||"No supported build target");
      setOutput(JSON.stringify(d,null,2)); onStatus?.("Build plan ready");
    }catch(e){setOutput(e.message);onStatus?.("Build planning failed")}
    finally{setBusy(false)}
  }
  async function executeBuild(){
    if(!workspace||buildBusy)return;
    setBuildBusy(true); setOutput("Secure Build Engine → syncing workspace → compiling → collecting artifacts…");
    try{
      const r=await apiFetch("/ide/workspaces/"+workspace.id+"/build/",{method:"POST",body:JSON.stringify({})});
      const d=await r.json();
      if(!r.ok)throw Error(d.error||d.detail||"Build failed");

      const summary=[d.status?.toUpperCase(),d.plan?.strategy?.command,d.exit_code===0?"BUILD VERIFIED":"BUILD FAILED",(d.artifacts||[]).length+" artifact(s)"].filter(Boolean).join(" · ");
      setOutput([summary,d.stdout,d.stderr].filter(Boolean).join("\n"));
      onStatus?.(d.exit_code===0?"Build verified":"Build failed");
    }catch(e){setOutput(e.message);onStatus?.("Build failed")}finally{setBuildBusy(false)}
  }
  function discoverTests(){
    const names=new Set(Object.keys(files||{}).map(p=>p.split("/").pop()));
    const result=[];
    if(names.has("package.json")) result.push({id:"npm",label:"Project tests",command:"npm test"});
    if(names.has("vitest.config.js")||names.has("vitest.config.ts")) result.push({id:"vitest",label:"Vitest",command:"npx vitest run --reporter=dot"});
    if(names.has("jest.config.js")||names.has("jest.config.ts")) result.push({id:"jest",label:"Jest",command:"npx jest --runInBand"});
    if(names.has("pyproject.toml")||names.has("pytest.ini")) result.push({id:"pytest",label:"Pytest",command:"python3 -m pytest -q"});
    if(names.has("go.mod")) result.push({id:"go",label:"Go tests",command:"go test ./..."});
    if(names.has("Cargo.toml")) result.push({id:"cargo",label:"Cargo tests",command:"cargo test"});
    if(names.has("pom.xml")) result.push({id:"maven",label:"Maven tests",command:"mvn -B test"});
    if(names.has("build.gradle")||names.has("build.gradle.kts")) result.push({id:"gradle",label:"Gradle tests",command:"gradle test"});
    setTests(result);setOutput(result.length?result.map(x=>"✓ "+x.label+"  →  "+x.command).join("\n"):"No supported test framework detected.");
  }
  async function runTest(test){
    if(!workspace||testBusy)return;
    setTestBusy(true);setOutput("Test Orchestrator → "+test.label+"…");
    try{
      const r=await apiFetch("/ide/workspaces/"+workspace.id+"/execute/",{method:"POST",body:JSON.stringify({command:test.command})});
      const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||"Test execution failed");
      setOutput([test.label,d.stdout,d.stderr,"exit_code="+d.exit_code].filter(Boolean).join("\n"));
      onStatus?.(d.exit_code===0?"Tests passed":"Tests failed");
    }catch(e){setOutput(e.message);onStatus?.("Test execution failed")}finally{setTestBusy(false)}
  }
  async function packageAction(){
    if(!workspace||busy)return;
    setBusy(true);setOutput("Compiling package operation → secure runner…");
    try{
      const r=await apiFetch("/ide/workspaces/"+workspace.id+"/packages/",{method:"POST",body:JSON.stringify({action,package_manager:manager?.id||workspace.package_manager||"",packages:packageName.split(",").map(x=>x.trim()).filter(Boolean)})});
      const d=await r.json();if(!r.ok)throw Error(d.error||d.detail||"Package operation failed");
      setOutput([d.command,d.stdout,d.stderr].filter(Boolean).join("\n"));
      onStatus?.(d.status==="success"?"Package operation completed":"Package operation failed");
    }catch(e){setOutput(e.message);onStatus?.("Package operation failed")}finally{setBusy(false)}
  }
  return <section className="dos-engineering-panel">
    <header><div><strong>ENGINEERING CONTROL PLANE</strong><small>PROJECT → PACKAGE → BUILD → TEST → DEBUG → RUN</small></div><button onClick={onClose}>×</button></header>
    <div className="dos-engineering-grid">
      <div><span>WORKSPACE</span><b>{workspace?.name||"—"}</b><small>{Object.keys(files||{}).length} source files</small></div>
      <div><span>PACKAGE ENGINE</span><b>{manager?.id||workspace?.package_manager||"auto-detect"}</b><small>{manager?.lockfile?"lockfile detected":"manifest strategy"}</small></div>
      <div><span>RUNNER</span><b>{caps?.sandbox?.mode||"negotiating…"}</b><small>{caps?.runtimes?Object.entries(caps.runtimes).filter(([,v])=>v.available).map(([k])=>k).join(" · "):"capability handshake"}</small></div>
    </div>
    <div className="dos-engineering-actions">
      <select value={action} onChange={e=>setAction(e.target.value)}><option value="install">Install</option><option value="add">Add</option><option value="remove">Remove</option><option value="update">Update</option></select>
      <input value={packageName} onChange={e=>setPackageName(e.target.value)} placeholder="package or comma-separated packages"/>
      <button onClick={packageAction} disabled={busy||!packageName.trim()}>{busy?"Working…":"Execute safely"}</button>
    </div>
    <div className="dos-test-explorer"><div><b>TEST EXPLORER</b><small>{tests.length} discovered</small></div><button onClick={discoverTests}>Discover</button>{tests.map(t=><div className="dos-test-row" key={t.id}><span>◉ {t.label}</span><code>{t.command}</code><button onClick={()=>runTest(t)} disabled={testBusy}>{testBusy?"…":"Run"}</button></div>)}</div><div className="dos-engineering-pipeline"><span>Detect</span><i>→</i><span>Plan</span><i>→</i><span>Provision</span><i>→</i><span>Build</span><i>→</i><span>Test</span><i>→</i><span>Verify</span></div>
    <pre className="dos-engineering-output">{output}</pre>
    <footer><span>🔒 Raw shell is never generated by the package UI.</span><button onClick={build} disabled={busy||buildBusy}>Plan Build</button><button onClick={executeBuild} disabled={busy||buildBusy||!workspace}>{buildBusy?"Building…":"Build & Verify"}</button></footer>
  </section>;
}
