import { useEffect, useRef } from "react";
import { API_URL, getAccessToken } from "./services/api";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

globalThis.MonacoEnvironment={getWorker(_id,label){if(label==="json")return new jsonWorker();if(["css","scss","less"].includes(label))return new cssWorker();if(["html","handlebars","razor"].includes(label))return new htmlWorker();if(["typescript","javascript"].includes(label))return new tsWorker();return new editorWorker()}};
monaco.languages.typescript.javascriptDefaults.setEagerModelSync(true);monaco.languages.typescript.typescriptDefaults.setEagerModelSync(true);
const compiler={target:monaco.languages.typescript.ScriptTarget.ES2022,allowNonTsExtensions:true,module:monaco.languages.typescript.ModuleKind.ESNext,moduleResolution:monaco.languages.typescript.ModuleResolutionKind.NodeJs,jsx:monaco.languages.typescript.JsxEmit.ReactJSX,allowJs:true,checkJs:false,strict:false};
monaco.languages.typescript.javascriptDefaults.setCompilerOptions(compiler);monaco.languages.typescript.typescriptDefaults.setCompilerOptions(compiler);
const extraLibs={
  javascript:[{content:'declare const process: any; declare const Buffer: any;'}],
  typescript:[{content:'declare const process: any; declare const Buffer: any;'}]
};
monaco.languages.typescript.javascriptDefaults.addExtraLib(extraLibs.javascript[0].content,"file:///developer-os/global.d.ts");
monaco.languages.typescript.typescriptDefaults.addExtraLib(extraLibs.typescript[0].content,"file:///developer-os/global.d.ts");

monaco.editor.defineTheme("developer-os-dark",{base:"vs-dark",inherit:true,rules:[{token:"comment",foreground:"4f7182",fontStyle:"italic"},{token:"keyword",foreground:"48ddff"},{token:"type",foreground:"8ad8ff"},{token:"string",foreground:"69f0b0"},{token:"number",foreground:"c69cff"},{token:"function",foreground:"a8e8ff"},{token:"variable",foreground:"d8edf5"}],colors:{"editor.background":"#02070c","editor.foreground":"#d8edf5","editorLineNumber.foreground":"#294451","editorLineNumber.activeForeground":"#4fe7ff","editorCursor.foreground":"#57efff","editor.selectionBackground":"#123c51","editor.inactiveSelectionBackground":"#0b2735","editor.lineHighlightBackground":"#06131b","editorIndentGuide.background1":"#0a202b","editorIndentGuide.activeBackground1":"#16475a","editorWidget.background":"#06131b","editorWidget.border":"#164457","editorSuggestWidget.background":"#06131b","editorSuggestWidget.border":"#1d5a70","editorSuggestWidget.selectedBackground":"#0c3444","editorHoverWidget.background":"#071923","editorHoverWidget.border":"#1b5368","editorBracketHighlight.foreground1":"#48ddff","editorBracketHighlight.foreground2":"#69f0b0","editorBracketHighlight.foreground3":"#c69cff","editorBracketHighlight.foreground4":"#ffcf70","editorOverviewRuler.border":"#00000000","minimap.background":"#030a10","minimap.selectionHighlight":"#164457","scrollbarSlider.background":"#12303c88","scrollbarSlider.hoverBackground":"#1c526688","scrollbarSlider.activeBackground":"#26758b99"}});
const lang=p=>({js:"javascript",jsx:"javascript",ts:"typescript",tsx:"typescript",py:"python",html:"html",htm:"html",css:"css",scss:"scss",json:"json",md:"markdown",yaml:"yaml",yml:"yaml",sql:"sql",sh:"shell",bash:"shell",go:"go",rs:"rust",java:"java",php:"php",xml:"xml",vue:"html",c:"c",cpp:"cpp",h:"cpp",hpp:"cpp",txt:"plaintext",env:"plaintext"}[p.split(".").pop()?.toLowerCase()]||"plaintext");


const importPathRegex=/((?:from\s+|import\s*\(\s*|require\s*\(\s*)["'])([^"']+)(["'])/g;
function workspaceSymbols(source){const symbols=[];const re=/\b(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)/g;let match;while((match=re.exec(source))&&symbols.length<500)symbols.push({name:match[1],offset:match.index});return symbols;}
function resolveWorkspaceImport(currentPath,spec){if(!spec||!spec.startsWith("."))return null;const parts=currentPath.split("/");parts.pop();const base=parts.concat(spec.split("/"));const out=[];for(const p of base){if(!p||p===".")continue;if(p==="..")out.pop();else out.push(p)}return out.join("/");}
export default function MonacoEditor({path,value,onChange,onCursorChange,diagnostics=[],remoteCursors=[],onWorkspaceSymbols,workspaceId}){
 const host=useRef(null),editor=useRef(null),model=useRef(null),change=useRef(onChange),cursor=useRef(onCursorChange),pathRef=useRef(path),remoteDecorations=useRef([]),models=useRef(new Map()),viewStates=useRef(new Map());
 useEffect(()=>{change.current=onChange;cursor.current=onCursorChange},[onChange,onCursorChange]);
 const lspRequest=async(method,params={},languageOverride=null)=>{if(!workspaceId)return null;const token=getAccessToken();try{const response=await fetch(API_URL+"/ide/workspaces/"+workspaceId+"/lsp/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({language:languageOverride||lang(pathRef.current),method,uri:monaco.Uri.parse("inmemory://developer-os/"+encodeURIComponent(pathRef.current)).toString(),params})});if(!response.ok)return null;return response.json().catch(()=>null)}catch{return null}};
 const request=async(action,body={})=>{if(!workspaceId)return {};const token=getAccessToken();try{const r=await fetch(API_URL+"/ide/workspaces/"+workspaceId+"/symbols/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({action,path:pathRef.current,...body})});if(!r.ok)return {};return r.json().catch(()=>({}))}catch{return {}}};
 useEffect(()=>{if(!host.current)return;const ed=monaco.editor.create(host.current,{model:null,theme:"developer-os-dark",automaticLayout:true,minimap:{enabled:true,side:"right",showSlider:"mouseover",renderCharacters:false},fontFamily:"JetBrains Mono, Consolas, monospace",fontSize:13,lineHeight:21,tabSize:2,insertSpaces:true,wordWrap:"off",smoothScrolling:true,cursorBlinking:"smooth",cursorSmoothCaretAnimation:"on",bracketPairColorization:{enabled:true},guides:{indentation:true,bracketPairs:true},folding:true,foldingStrategy:"auto",stickyScroll:{enabled:true},renderWhitespace:"selection",scrollBeyondLastLine:false,padding:{top:10,bottom:18},quickSuggestions:{other:true,comments:false,strings:true},suggestOnTriggerCharacters:true,parameterHints:{enabled:true},formatOnPaste:true,formatOnType:true,contextmenu:true,multiCursorModifier:"ctrlCmd",mouseWheelZoom:true,overviewRulerBorder:false,scrollbar:{verticalScrollbarSize:10,horizontalScrollbarSize:10,useShadows:false},find:{addExtraSpaceOnTop:true,autoFindInSelection:"never"},occurrencesHighlight:"singleFile",selectionHighlight:true,renderLineHighlight:"all",fixedOverflowWidgets:true,linkedEditing:true,suggest:{showMethods:true,showFunctions:true,showConstructors:true},hover:{enabled:true},inlayHints:{enabled:"on"},codeLens:true});editor.current=ed;
 const p1Providers=[];
 const lspRequest=async(method,params={},languageOverride=null)=>{
  if(!workspaceId)return null;
  const token=getAccessToken();
  const response=await fetch(API_URL+"/ide/workspaces/"+workspaceId+"/lsp/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({language:languageOverride||lang(pathRef.current),method,uri:monaco.Uri.parse("inmemory://developer-os/"+encodeURIComponent(pathRef.current)).toString(),params})});
  if(!response.ok)return null;
  return response.json().catch(()=>null);
 };
 const registerP1=()=>{
  if(!workspaceId)return;
  const token=getAccessToken();
  const request=async(action,body={})=>{const r=await fetch(API_URL+"/ide/workspaces/"+workspaceId+"/symbols/",{method:"POST",headers:{"Content-Type":"application/json","Authorization":token?"Bearer "+token:""},body:JSON.stringify({action,path:pathRef.current,...body})});if(!r.ok)return {};return r.json().catch(()=>({}));};
  ["javascript","typescript","python"].forEach(language=>{
   p1Providers.push(monaco.languages.registerDefinitionProvider(language,{provideDefinition:async(model,pos)=>{const l=lang(pathRef.current);const ld=await lspRequest("textDocument/definition",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1}},l);const lr=ld?.result; if(lr){const items=Array.isArray(lr)?lr:(lr.uri?[lr]:[]);return items.map(x=>({uri:monaco.Uri.parse(x.uri||x.targetUri),range:{startLineNumber:x.range.start.line+1,endLineNumber:x.range.end.line+1,startColumn:x.range.start.character+1,endColumn:x.range.end.character+1}}));} const word=model.getWordAtPosition(pos);if(!word)return null;const d=await request("definitions",{name:word.word,line:pos.lineNumber});return (d.definitions||[]).map(x=>({uri:monaco.Uri.parse("inmemory://developer-os/"+encodeURIComponent(x.path)),range:{startLineNumber:x.line,endLineNumber:x.line,startColumn:x.column||1,endColumn:(x.column||1)+word.word.length}}));}}));
   p1Providers.push(monaco.languages.registerHoverProvider(language,{provideHover:async(model,pos)=>{const l=lang(pathRef.current);const ld=await lspRequest("textDocument/hover",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1}},l);if(ld?.result){const contents=Array.isArray(ld.result.contents)?ld.result.contents: [ld.result.contents];return {contents:contents.map(x=>({value:typeof x==="string"?x:(x?.value||"")}))};} const word=model.getWordAtPosition(pos);if(!word)return null;const d=await request("hover",{name:word.word,line:pos.lineNumber});return d.hover?{contents:[{value:d.hover.markdown||d.hover.signature||word.word}]}:null;}}));
   p1Providers.push(monaco.languages.registerReferenceProvider(language,{provideReferences:async(model,pos,context)=>{
    const ld=await lspRequest("textDocument/references",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1},context:{includeDeclaration:!!context.includeDeclaration}},language);
    const refs=Array.isArray(ld?.result)?ld.result:[];
    return refs.map(x=>({uri:monaco.Uri.parse(x.uri),range:{startLineNumber:x.range.start.line+1,endLineNumber:x.range.end.line+1,startColumn:x.range.start.character+1,endColumn:x.range.end.character+1}}));
  }}));
  p1Providers.push(monaco.languages.registerRenameProvider(language,{provideRenameEdits:async(model,pos,newName)=>{
    const ld=await lspRequest("textDocument/rename",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1},newName},language);
    const changes=ld?.result?.changes||{};
    const edits=[];
    for(const [uri,items] of Object.entries(changes)) for(const x of (items||[])) edits.push({resource:monaco.Uri.parse(uri),textEdit:{range:{startLineNumber:x.range.start.line+1,endLineNumber:x.range.end.line+1,startColumn:x.range.start.character+1,endColumn:x.range.end.character+1},text:x.newText}});
    return {edits};
  }}));
  p1Providers.push(monaco.languages.registerCompletionItemProvider(language,{triggerCharacters:[".","_"],provideCompletionItems:async(model,pos)=>{const l=lang(pathRef.current);const ld=await lspRequest("textDocument/completion",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1}},l);const live=ld?.result?.items||(Array.isArray(ld?.result)?ld.result:[]);if(live.length)return {suggestions:live.slice(0,200).map(x=>({label:x.label,kind:monaco.languages.CompletionItemKind.Text,detail:x.detail||"",insertText:x.insertText||x.label}))};const word=model.getWordUntilPosition(pos);const d=await request("completion",{query:word.word,line:pos.lineNumber});return {suggestions:(d.completions||[]).map(x=>({label:x.label,kind:monaco.languages.CompletionItemKind.Text,detail:x.detail,insertText:x.label,range:{startLineNumber:pos.lineNumber,startColumn:word.startColumn,endLineNumber:pos.lineNumber,endColumn:word.endColumn}}))};}}));
  });
 };
 registerP1();
 const p1Diagnostics=ed.createDecorationsCollection([]);
 const applyLspDiagnostics=(payload)=>{
   const notes=payload?.notifications||[];
   for(const n of notes){
     if(n?.method!=="textDocument/publishDiagnostics")continue;
     const uri=n.params?.uri||"";
     const current=ed.getModel();
     if(!current||current.uri.toString()!==uri)continue;
     const marks=(n.params?.diagnostics||[]).map(x=>({severity:x.severity===1?monaco.MarkerSeverity.Error:x.severity===2?monaco.MarkerSeverity.Warning:x.severity===3?monaco.MarkerSeverity.Info:monaco.MarkerSeverity.Hint,message:x.message||"Diagnostic",startLineNumber:x.range.start.line+1,startColumn:x.range.start.character+1,endLineNumber:x.range.end.line+1,endColumn:x.range.end.character+1,source:x.source||"LSP",code:x.code||""}));
     monaco.editor.setModelMarkers(current,"lsp",marks);
   }
 };
 const lspRequestWithDiagnostics=async(method,params={},languageOverride=null)=>{
   const data=await lspRequest(method,params,languageOverride); applyLspDiagnostics(data); return data;
 };
 const refreshP1Diagnostics=async()=>{if(!workspaceId)return;const d=await request("diagnostics",{});const markers=(d.diagnostics||[]).filter(x=>x.path===pathRef.current).map(x=>({severity:x.severity==="error"?monaco.MarkerSeverity.Error:x.severity==="warning"?monaco.MarkerSeverity.Warning:monaco.MarkerSeverity.Info,message:x.message||"",startLineNumber:x.line||1,startColumn:x.column||1,endLineNumber:x.line||1,endColumn:(x.column||1)+1,source:x.source||"Developer OS",code:x.code||""}));monaco.editor.setModelMarkers(ed.getModel(),"developer-os",markers);p1Diagnostics.set(markers.map(x=>({range:{startLineNumber:x.startLineNumber,startColumn:x.startColumn,endLineNumber:x.endLineNumber,endColumn:x.endColumn},options:{inlineClassName:"developer-os-diagnostic"}})));};
 refreshP1Diagnostics();
 const p1Actions=ed.addAction({id:"developer-os.rename-preview",label:"Developer OS: Rename with Diff Preview",keybindings:[monaco.KeyCode.F2],run:e=>{const m=e.getModel(),pos=e.getPosition();const word=m?.getWordAtPosition(pos);if(word)window.dispatchEvent(new CustomEvent("developer-os:rename-preview",{detail:{old:word.word,path:pathRef.current}}));}});
 const p1Refs=ed.addAction({id:"developer-os.references-panel",label:"Developer OS: Find References (Developer OS)",keybindings:[monaco.KeyMod.Shift|monaco.KeyCode.F12],run:e=>{const m=e.getModel(),pos=e.getPosition();const word=m?.getWordAtPosition(pos);if(word)window.dispatchEvent(new CustomEvent("developer-os:find-references",{detail:{name:word.word,path:pathRef.current}}));}});
 let lspVersion=1,lspTimer=null;
 const syncOpen=()=>{const m=ed.getModel();if(m&&workspaceId)void lspRequest("textDocument/didOpen",{textDocument:{uri:m.uri.toString(),languageId:lang(pathRef.current),version:lspVersion,text:m.getValue()}});};
 syncOpen();
 const c=ed.onDidChangeModelContent(()=>{change.current?.(ed.getValue());clearTimeout(lspTimer);lspTimer=setTimeout(()=>{const m=ed.getModel();if(m&&workspaceId)void lspRequestWithDiagnostics("textDocument/didChange",{textDocument:{uri:m.uri.toString(),version:++lspVersion},contentChanges:[{text:m.getValue()}]});},300);});
 const p=ed.onDidChangeCursorPosition(e=>cursor.current?.({line:e.position.lineNumber,column:e.position.column,path:pathRef.current}));
 const formatRequest=async(pathName,content)=>{
   if(!workspaceId)return null;
   const token=getAccessToken();
   try{
    const response=await fetch(API_URL+"/ide/workspaces/"+workspaceId+"/format/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({path:pathName,content})});
    if(!response.ok)return null;
    return response.json().catch(()=>null);
   }catch{return null}
 };
 const codeActionsRequest=async(model,range)=>{
   if(!workspaceId)return [];
   const token=getAccessToken();
   try{
    const response=await fetch(API_URL+"/ide/workspaces/"+workspaceId+"/symbols/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({action:"code_actions",path:pathRef.current,line:range.startLineNumber})});
    if(!response.ok)return [];
    const data=await response.json().catch(()=>({}));
    return Array.isArray(data.actions)?data.actions:[];
   }catch{return []}
 };
 const codeActionProviders=["python","javascript","typescript"].map(language=>monaco.languages.registerCodeActionProvider(language,{
   provideCodeActions:async(model,range)=>{
     const actions=await codeActionsRequest(model,range);
     return {actions:actions.map((a,index)=>({title:a.title||a.description||("Code Action "+(index+1)),kind:a.kind||"quickfix",diagnostics:[],edit:a.edit?.changes?{edits:Object.entries(a.edit.changes).flatMap(([uri,items])=>(items||[]).map(x=>({resource:monaco.Uri.parse(uri),textEdit:{range:{startLineNumber:x.range.start.line+1,endLineNumber:x.range.end.line+1,startColumn:x.range.start.character+1,endColumn:x.range.end.character+1},text:x.newText}})))}:undefined,command:a.command}))};
   }
 }));
 const formatProvider=monaco.languages.registerDocumentFormattingEditProvider("python",{provideDocumentFormattingEdits:async(model)=>{
   const r=await formatRequest(pathRef.current,model.getValue());
   return r?.changed?[{range:model.getFullModelRange(),text:r.content}]:[];
 }});
 ["javascript","typescript","json","css","scss","html"].forEach(language=>{
   p1Providers.push(monaco.languages.registerDocumentFormattingEditProvider(language,{provideDocumentFormattingEdits:async(model)=>{
     const r=await formatRequest(pathRef.current,model.getValue());
     return r?.changed?[{range:model.getFullModelRange(),text:r.content}]:[];
   }}));
 });
 const format=ed.addAction({id:"developer-os.format-document",label:"Developer OS: Format Document",keybindings:[monaco.KeyMod.Shift|monaco.KeyMod.Alt|monaco.KeyCode.KeyF],run:async e=>{try{await e.getAction("editor.action.formatDocument")?.run()}catch{return}}});
 const explain=ed.addAction({id:"developer-os.explain-selection",label:"Developer OS: Explain Selection",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyMod.Shift|monaco.KeyCode.KeyE],run:e=>{const s=e.getSelection();const text=s?e.getModel()?.getValueInRange(s):"";window.dispatchEvent(new CustomEvent("developer-os:ai-action",{detail:{action:"explain",path:pathRef.current,code:text||e.getValue()}}));}});
 const fix=ed.addAction({id:"developer-os.fix-selection",label:"Developer OS: Fix Selection",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyK],run:e=>{const s=e.getSelection();window.dispatchEvent(new CustomEvent("developer-os:ai-action",{detail:{action:"fix",path:pathRef.current,code:s?e.getModel()?.getValueInRange(s):e.getValue()}}));}});
 const completionAction=ed.addAction({id:"developer-os.ai-complete",label:"Developer OS: AI Complete",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyCode.Space],run:e=>{window.dispatchEvent(new CustomEvent("developer-os:ai-action",{detail:{action:"complete",path:pathRef.current,code:e.getValue(),position:e.getPosition()}}));}});
 const duplicate=ed.addAction({id:"developer-os.duplicate-line",label:"Developer OS: Duplicate Line",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyD],run:e=>{const s=e.getSelection();if(!s)return;e.executeEdits("developer-os",[{range:s,text:e.getModel()?.getValueInRange(s)+"\n",forceMoveMarkers:true}]);}});
const gotoLine=ed.addAction({id:"developer-os.goto-line",label:"Developer OS: Go to Line",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyL],run:e=>{const p=e.getPosition();if(p)e.revealPositionInCenter(p);e.focus();}});
 const foldAll=ed.addAction({id:"developer-os.fold-all",label:"Developer OS: Fold All",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyMod.Shift|monaco.KeyCode.BracketLeft],run:e=>e.getAction("editor.foldAll")?.run()});
 const unfoldAll=ed.addAction({id:"developer-os.unfold-all",label:"Developer OS: Unfold All",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyMod.Shift|monaco.KeyCode.BracketRight],run:e=>e.getAction("editor.unfoldAll")?.run()});
const symbolOutline=ed.addAction({id:"developer-os.symbol-outline",label:"Developer OS: Show Symbol Outline",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyO],run:e=>{e.getAction("editor.action.quickOutline")?.run();e.focus();}});
 const focusBreadcrumb=ed.addAction({id:"developer-os.focus-breadcrumbs",label:"Developer OS: Focus Breadcrumbs",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyB],run:e=>{e.getAction("breadcrumbs.focus")?.run();e.focus();}});
 const renameSelection=ed.addAction({id:"developer-os.rename-symbol",label:"Developer OS: Rename Symbol",keybindings:[monaco.KeyCode.F2],run:e=>{e.getAction("editor.action.rename")?.run();}});
 const references=ed.addAction({id:"developer-os.find-references",label:"Developer OS: Find References",keybindings:[monaco.KeyMod.Shift|monaco.KeyCode.F12],run:e=>{e.getAction("editor.action.referenceSearch.trigger")?.run();}});
 const openImport=ed.addAction({id:"developer-os.open-import",label:"Developer OS: Open Workspace Import",run:e=>{const m=e.getModel(),p=e.getPosition();if(!m||!p)return;const line=m.getLineContent(p.lineNumber),before=line.slice(0,p.column-1);let hit=null;for(const match of before.matchAll(importPathRegex)){hit=match}if(!hit)return;const target=resolveWorkspaceImport(pathRef.current,hit[2]);if(target)window.dispatchEvent(new CustomEvent("developer-os:open-file",{detail:{path:target}}));e.focus();}});
 const workspaceSymbolsAction=ed.addAction({id:"developer-os.workspace-symbols",label:"Developer OS: Workspace Symbols",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyMod.Shift|monaco.KeyCode.KeyO],run:async e=>{const m=e.getModel();if(!m)return;let remote=[];try{const d=await request("symbols",{query:""});remote=Array.isArray(d?.symbols)?d.symbols:[]}catch(error){void error}const local=workspaceSymbols(m.getValue()).map((s,index)=>({name:s.name,line:m.getPositionAt(s.offset).lineNumber,column:m.getPositionAt(s.offset).column,index,path:pathRef.current,kind:"local"}));const merged=(remote.length?remote:local).slice(0,500);onWorkspaceSymbols?.(merged.map((s,index)=>({name:s.name,line:Number(s.line||1),column:Number(s.column||1),index,path:s.path||pathRef.current,kind:s.kind||"symbol"})));window.dispatchEvent(new CustomEvent("developer-os:workspace-symbols",{detail:{path:pathRef.current,symbols:merged}}));e.focus();}});
  return()=>{clearTimeout(lspTimer);if(workspaceId&&ed.__dosLspOpened)for(const uri of ed.__dosLspOpened)void lspRequest("textDocument/didClose",{textDocument:{uri}});p1Providers.forEach(x=>x.dispose());codeActionProviders.forEach(x=>x.dispose());formatProvider.dispose();p1Actions.dispose();p1Refs.dispose();p1Diagnostics.clear();c.dispose();p.dispose();format.dispose();duplicate.dispose();explain.dispose();fix.dispose();completionAction.dispose();gotoLine.dispose();foldAll.dispose();unfoldAll.dispose();symbolOutline.dispose();focusBreadcrumb.dispose();renameSelection.dispose();references.dispose();openImport.dispose();workspaceSymbolsAction.dispose();for(const m of models.current.values())if(!m.isDisposed())m.dispose();models.current.clear();viewStates.current.clear();ed.dispose();editor.current=null;model.current=null}},[]);
 useEffect(()=>{const ed=editor.current;if(!ed)return;const normalized=path||"untitled";const previous=pathRef.current;if(previous&&ed.getModel()===model.current){viewStates.current.set(previous,ed.saveViewState());}pathRef.current=normalized;const uri=monaco.Uri.parse(`inmemory://developer-os/${encodeURIComponent(normalized)}`);let next=models.current.get(normalized);if(!next||next.isDisposed()){next=monaco.editor.getModel(uri)||monaco.editor.createModel(value||"",lang(normalized),uri);models.current.set(normalized,next);}else{monaco.editor.setModelLanguage(next,lang(normalized));}if(next.getValue()!==(value||"")&&!ed.hasTextFocus())next.setValue(value||"");if(ed.getModel()!==next)ed.setModel(next);model.current=next;
  if(workspaceId&&["python","javascript","typescript"].includes(lang(normalized))){
    const uriString=next.uri.toString();
    if(!ed.__dosLspOpened)ed.__dosLspOpened=new Set();
    if(!ed.__dosLspOpened.has(uriString)){
      ed.__dosLspOpened.add(uriString);
      void lspRequest("textDocument/didOpen",{textDocument:{uri:uriString,languageId:lang(normalized),version:1,text:next.getValue()}},lang(normalized));
    }
  }const saved=viewStates.current.get(normalized);if(saved)ed.restoreViewState(saved);ed.focus();return()=>{if(model.current===next)model.current=null}},[path]);
 useEffect(()=>{const m=model.current;if(m&&m.getValue()!==(value||"")&&!editor.current?.hasTextFocus())m.setValue(value||"")},[value]);
 useEffect(()=>{const ed=editor.current;if(!ed)return;const reveal=e=>{const line=Math.max(1,Number(e.detail?.line||1));const column=Math.max(1,Number(e.detail?.column||1));ed.revealPositionInCenter({lineNumber:line,column});ed.setPosition({lineNumber:line,column});ed.focus()};const focusEditor=()=>{if(ed.getModel()){ed.focus();ed.revealPositionInCenter(ed.getPosition()||{lineNumber:1,column:1})}};window.addEventListener("developer-os:reveal-line",reveal);window.addEventListener("developer-os:focus-editor",focusEditor);return()=>{window.removeEventListener("developer-os:reveal-line",reveal);window.removeEventListener("developer-os:focus-editor",focusEditor)}},[path]);
 useEffect(()=>{const m=model.current;if(!m)return;const markers=(diagnostics||[]).filter(x=>!x.path||x.path===path).map(x=>({startLineNumber:Math.max(1,x.line||1),endLineNumber:Math.max(1,x.end_line||x.line||1),startColumn:Math.max(1,x.column||1),endColumn:Math.max(2,x.end_column||((x.column||1)+1)),message:x.message||"Diagnostic",severity:x.severity==="warning"?monaco.MarkerSeverity.Warning:x.severity==="info"?monaco.MarkerSeverity.Info:monaco.MarkerSeverity.Error}));monaco.editor.setModelMarkers(m,"developer-os",markers);return()=>monaco.editor.setModelMarkers(m,"developer-os",[])},[diagnostics,path]);

 useEffect(()=>{const ed=editor.current;if(!ed)return;const dec=(remoteCursors||[]).filter(x=>x.cursor?.path===path||x.cursor?.file===path).map(x=>{const line=Math.max(1,Number(x.cursor.line||1));const col=Math.max(1,Number(x.cursor.column||1));return {range:new monaco.Range(line,col,line,col),options:{className:"dos-remote-cursor",hoverMessage:{value:"**"+(x.username||"Developer")+"**"},afterContentClassName:"dos-remote-cursor-label",stickiness:monaco.editor.TrackedRangeStickiness.Never}}});remoteDecorations.current=ed.deltaDecorations(remoteDecorations.current,dec);return()=>{remoteDecorations.current=ed.deltaDecorations(remoteDecorations.current,[])}},[remoteCursors,path]);
 return <div ref={host} className="monaco-host" aria-label={`Developer OS code editor ${path||"untitled"}`}/>;
}


// Remote cursor decorations are lightweight Monaco decorations driven by collaboration presence.
