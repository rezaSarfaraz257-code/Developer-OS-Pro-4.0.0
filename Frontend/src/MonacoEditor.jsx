import { useEffect, useRef, useState } from "react";
import { API_URL, getAccessToken } from "./services/api";
import * as monaco from "monaco-editor";
import { mapLspCompletionItem, mapLspWorkspaceEdit, mapLspLocations, mapLspDiagnostics, mapLspDocumentHighlights, mapLspInlayHints, mapLspSignatureHelp, nextLspDocumentVersion } from "./ide/monacoCompletion.js";
import editorWorker from "monaco-editor/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/language/json/json.worker?worker";
import cssWorker from "monaco-editor/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/language/html/html.worker?worker";
import tsWorker from "monaco-editor/language/typescript/ts.worker?worker";

globalThis.MonacoEnvironment={getWorker(_id,label){if(label==="json")return new jsonWorker();if(["css","scss","less"].includes(label))return new cssWorker();if(["html","handlebars","razor"].includes(label))return new htmlWorker();if(["typescript","javascript"].includes(label))return new tsWorker();return new editorWorker()}};
// Monaco 0.55+ exposes language services under monaco.typescript.
// Keep the application shell bootable if a custom/partial Monaco bundle omits them.
if (monaco.typescript?.javascriptDefaults && monaco.typescript?.typescriptDefaults) {
  monaco.typescript.javascriptDefaults.setEagerModelSync(true);monaco.typescript.typescriptDefaults.setEagerModelSync(true);
  const compiler={target:monaco.typescript.ScriptTarget.ES2022,allowNonTsExtensions:true,module:monaco.typescript.ModuleKind.ESNext,moduleResolution:monaco.typescript.ModuleResolutionKind.NodeJs,jsx:monaco.typescript.JsxEmit.ReactJSX,allowJs:true,checkJs:false,strict:false};
  monaco.typescript.javascriptDefaults.setCompilerOptions(compiler);monaco.typescript.typescriptDefaults.setCompilerOptions(compiler);
  const extraLibs={
    javascript:[{content:'declare const process: any; declare const Buffer: any;'}],
    typescript:[{content:'declare const process: any; declare const Buffer: any;'}]
  };
  monaco.typescript.javascriptDefaults.addExtraLib(extraLibs.javascript[0].content,"file:///developer-os/global.d.ts");
  monaco.typescript.typescriptDefaults.addExtraLib(extraLibs.typescript[0].content,"file:///developer-os/global.d.ts");
}

monaco.editor.defineTheme("developer-os-dark",{base:"vs-dark",inherit:true,rules:[{token:"comment",foreground:"4f7182",fontStyle:"italic"},{token:"keyword",foreground:"48ddff"},{token:"type",foreground:"8ad8ff"},{token:"string",foreground:"69f0b0"},{token:"number",foreground:"c69cff"},{token:"function",foreground:"a8e8ff"},{token:"variable",foreground:"d8edf5"}],colors:{"editor.background":"#02070c","editor.foreground":"#d8edf5","editorLineNumber.foreground":"#294451","editorLineNumber.activeForeground":"#4fe7ff","editorCursor.foreground":"#57efff","editor.selectionBackground":"#123c51","editor.inactiveSelectionBackground":"#0b2735","editor.lineHighlightBackground":"#06131b","editorIndentGuide.background1":"#0a202b","editorIndentGuide.activeBackground1":"#16475a","editorWidget.background":"#06131b","editorWidget.border":"#164457","editorSuggestWidget.background":"#06131b","editorSuggestWidget.border":"#1d5a70","editorSuggestWidget.selectedBackground":"#0c3444","editorHoverWidget.background":"#071923","editorHoverWidget.border":"#1b5368","editorBracketHighlight.foreground1":"#48ddff","editorBracketHighlight.foreground2":"#69f0b0","editorBracketHighlight.foreground3":"#c69cff","editorBracketHighlight.foreground4":"#ffcf70","editorOverviewRuler.border":"#00000000","minimap.background":"#030a10","minimap.selectionHighlight":"#164457","scrollbarSlider.background":"#12303c88","scrollbarSlider.hoverBackground":"#1c526688","scrollbarSlider.activeBackground":"#26758b99"}});
const lang=p=>({js:"javascript",jsx:"javascript",ts:"typescript",tsx:"typescript",py:"python",html:"html",htm:"html",css:"css",scss:"scss",json:"json",md:"markdown",yaml:"yaml",yml:"yaml",sql:"sql",sh:"shell",bash:"shell",go:"go",rs:"rust",java:"java",php:"php",xml:"xml",vue:"html",c:"c",cpp:"cpp",h:"cpp",hpp:"cpp",txt:"plaintext",env:"plaintext"}[p.split(".").pop()?.toLowerCase()]||"plaintext");


const importPathRegex=/((?:from\s+|import\s*\(\s*|require\s*\(\s*)["'])([^"']+)(["'])/g;
function workspaceSymbols(source){const symbols=[];const re=/\b(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)/g;let match;while((match=re.exec(source))&&symbols.length<500)symbols.push({name:match[1],offset:match.index});return symbols;}
function resolveWorkspaceImport(currentPath,spec){if(!spec||!spec.startsWith("."))return null;const parts=currentPath.split("/");parts.pop();const base=parts.concat(spec.split("/"));const out=[];for(const p of base){if(!p||p===".")continue;if(p==="..")out.pop();else out.push(p)}return out.join("/");}
export default function MonacoEditor({path,value,onChange,onCursorChange,diagnostics=[],remoteCursors=[],onWorkspaceSymbols,workspaceId,editorSettings={}}){
 const [editorReady,setEditorReady]=useState(false);
 const host=useRef(null),editor=useRef(null),model=useRef(null),change=useRef(onChange),cursor=useRef(onCursorChange),pathRef=useRef(path),workspaceIdRef=useRef(workspaceId),modelWorkspaceRef=useRef(workspaceId),remoteDecorations=useRef([]),models=useRef(new Map()),viewStates=useRef(new Map()),lspTimerRef=useRef(null);
 useEffect(()=>{workspaceIdRef.current=workspaceId},[workspaceId]);
 useEffect(()=>{change.current=typeof onChange==="function"?onChange:null;cursor.current=typeof onCursorChange==="function"?onCursorChange:null},[onChange,onCursorChange]);
 const lspRequest=async(method,params={},languageOverride=null,originModel=null)=>{
  const requestWorkspaceId=workspaceIdRef.current;
  const requestPath=pathRef.current;
  if(!requestWorkspaceId||(originModel&&editor.current?.getModel()!==originModel))return null;
  const token=getAccessToken();
  const requestUri=params?.textDocument?.uri||monaco.Uri.parse("inmemory://developer-os/"+encodeURIComponent(requestPath)).toString();
  try{
   const response=await fetch(API_URL+"/ide/workspaces/"+requestWorkspaceId+"/lsp/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({language:languageOverride||lang(requestPath),method,uri:requestUri,params})});
   if(!response.ok||workspaceIdRef.current!==requestWorkspaceId||(originModel&&(pathRef.current!==requestPath||editor.current?.getModel()!==originModel)))return null;
   const data=await response.json().catch(()=>null);
   return workspaceIdRef.current===requestWorkspaceId&&(!originModel||(pathRef.current===requestPath&&editor.current?.getModel()===originModel))?data:null;
  }catch{return null}
 };
 const request=async(action,body={})=>{const requestWorkspaceId=workspaceIdRef.current;const requestPath=pathRef.current;if(!requestWorkspaceId)return {};const token=getAccessToken();try{const r=await fetch(API_URL+"/ide/workspaces/"+requestWorkspaceId+"/symbols/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({action,path:requestPath,...body})});if(!r.ok||workspaceIdRef.current!==requestWorkspaceId||pathRef.current!==requestPath)return {};const data=await r.json().catch(()=>({}));return workspaceIdRef.current===requestWorkspaceId&&pathRef.current===requestPath?data:{}}catch{return {}}};
 useEffect(()=>{if(!host.current)return;const ed=monaco.editor.create(host.current,{model:null,theme:"developer-os-dark",automaticLayout:true,minimap:{enabled:true,side:"right",showSlider:"mouseover",renderCharacters:false},fontFamily:"JetBrains Mono, Consolas, monospace",fontSize:13,lineHeight:21,tabSize:2,insertSpaces:true,wordWrap:"off",smoothScrolling:true,cursorBlinking:"smooth",cursorSmoothCaretAnimation:"on",bracketPairColorization:{enabled:true},guides:{indentation:true,bracketPairs:true,highlightActiveBracketPair:true},folding:true,foldingStrategy:"auto",stickyScroll:{enabled:true},renderWhitespace:"selection",scrollBeyondLastLine:false,padding:{top:10,bottom:18},quickSuggestions:{other:true,comments:false,strings:true},suggestOnTriggerCharacters:true,parameterHints:{enabled:true},formatOnPaste:true,formatOnType:true,contextmenu:true,multiCursorModifier:"ctrlCmd",mouseWheelZoom:false,overviewRulerBorder:false,scrollbar:{verticalScrollbarSize:10,horizontalScrollbarSize:10,useShadows:false},find:{addExtraSpaceOnTop:true,autoFindInSelection:"never"},occurrencesHighlight:"singleFile",selectionHighlight:true,renderLineHighlight:"line",fixedOverflowWidgets:true,linkedEditing:true,suggest:{showMethods:true,showFunctions:true,showConstructors:true},hover:{enabled:true},inlayHints:{enabled:"on"},codeLens:true});editor.current=ed;setEditorReady(true);
 const p1Providers=[];
 let pendingLspChange=null;
 let lspFlushPromise=null;
 let applyLspDiagnostics=()=>{};
 const lspRequest=async(method,params={},languageOverride=null,originModel=null)=>{
  const requestWorkspaceId=workspaceIdRef.current;
  const requestPath=pathRef.current;
  if(!requestWorkspaceId||(originModel&&ed.getModel()!==originModel))return null;
  if(method!=="textDocument/didChange"&&!(await flushPendingLspChange()))return null;
  if(workspaceIdRef.current!==requestWorkspaceId||(originModel&&(pathRef.current!==requestPath||ed.getModel()!==originModel)))return null;
  const token=getAccessToken();
  const requestUri=params?.textDocument?.uri||monaco.Uri.parse("inmemory://developer-os/"+encodeURIComponent(pathRef.current)).toString();
  try{
   const response=await fetch(API_URL+"/ide/workspaces/"+requestWorkspaceId+"/lsp/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({language:languageOverride||lang(requestPath),method,uri:requestUri,params})});
   if(!response.ok||workspaceIdRef.current!==requestWorkspaceId||(originModel&&(pathRef.current!==requestPath||ed.getModel()!==originModel)))return null;
   const data=await response.json().catch(()=>null);
   return workspaceIdRef.current===requestWorkspaceId&&(!originModel||(pathRef.current===requestPath&&ed.getModel()===originModel))?data:null;
  }catch{return null}
 };
 const flushPendingLspChange=async()=>{
  if(lspFlushPromise){await lspFlushPromise;return flushPendingLspChange();}
  const pending=pendingLspChange;
  if(!pending)return true;
  pendingLspChange=null;
  clearTimeout(lspTimerRef.current);lspTimerRef.current=null;
  lspFlushPromise=(async()=>{
   if(workspaceIdRef.current!==pending.workspaceId||pathRef.current!==pending.path||ed.getModel()!==pending.model||!ed.__dosLspOpened?.has(pending.uri))return true;
   if(!ed.__dosLspVersions)ed.__dosLspVersions=new Map();
   const version=nextLspDocumentVersion(ed.__dosLspVersions,pending.uri);
   const data=await lspRequest("textDocument/didChange",{
    textDocument:{uri:pending.uri,version},
    contentChanges:[{text:pending.model.getValue()}],
   },pending.language,pending.model);
   if(!data){
    if(workspaceIdRef.current===pending.workspaceId&&pathRef.current===pending.path&&ed.getModel()===pending.model&&ed.__dosLspOpened?.has(pending.uri))pendingLspChange=pending;
    return pendingLspChange!==pending;
   }
   applyLspDiagnostics(data);
   return true;
  })();
  const result=await lspFlushPromise;
  lspFlushPromise=null;
  if(pendingLspChange)return flushPendingLspChange();
  return result;
 };
 const registerP1=()=>{
  const request=async(action,body={},originModel=null)=>{const requestWorkspaceId=workspaceIdRef.current;const requestPath=pathRef.current;if(!requestWorkspaceId||(originModel&&ed.getModel()!==originModel))return {};const token=getAccessToken();try{const r=await fetch(API_URL+"/ide/workspaces/"+requestWorkspaceId+"/symbols/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({action,path:requestPath,...body})});if(!r.ok||workspaceIdRef.current!==requestWorkspaceId||pathRef.current!==requestPath||(originModel&&ed.getModel()!==originModel))return {};const data=await r.json().catch(()=>({}));return workspaceIdRef.current===requestWorkspaceId&&pathRef.current===requestPath&&(!originModel||ed.getModel()===originModel)?data:{}}catch{return {}}};
  ["javascript","typescript","python"].forEach(language=>{
   p1Providers.push(monaco.languages.registerDefinitionProvider(language,{provideDefinition:async(model,pos)=>{const l=lang(pathRef.current);const ld=await lspRequest("textDocument/definition",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1}},l,model);const lr=ld?.result; if(lr)return mapLspLocations(lr,monaco); const word=model.getWordAtPosition(pos);if(!word)return null;const d=await request("definitions",{name:word.word,line:pos.lineNumber},model);return (d.definitions||[]).map(x=>({uri:monaco.Uri.parse("inmemory://developer-os/"+encodeURIComponent(x.path)),range:{startLineNumber:x.line,endLineNumber:x.line,startColumn:x.column||1,endColumn:(x.column||1)+word.word.length}}));}}));
   p1Providers.push(monaco.languages.registerHoverProvider(language,{provideHover:async(model,pos)=>{const l=lang(pathRef.current);const ld=await lspRequest("textDocument/hover",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1}},l,model);if(ld?.result){const contents=Array.isArray(ld.result.contents)?ld.result.contents: [ld.result.contents];return {contents:contents.map(x=>({value:typeof x==="string"?x:(x?.value||"")}))};} const word=model.getWordAtPosition(pos);if(!word)return null;const d=await request("hover",{name:word.word,line:pos.lineNumber},model);return d.hover?{contents:[{value:d.hover.markdown||d.hover.signature||word.word}]}:null;}}));
   p1Providers.push(monaco.languages.registerReferenceProvider(language,{provideReferences:async(model,pos,context)=>{
    const ld=await lspRequest("textDocument/references",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1},context:{includeDeclaration:!!context.includeDeclaration}},language,model);
    return mapLspLocations(ld?.result,monaco);
  }}));
  p1Providers.push(monaco.languages.registerDocumentHighlightProvider(language,{
    provideDocumentHighlights:async(model,pos)=>{
      const response=await lspRequest("textDocument/documentHighlight",{
        textDocument:{uri:model.uri.toString()},
        position:{line:pos.lineNumber-1,character:pos.column-1},
      },language,model);
      return mapLspDocumentHighlights(response?.result,monaco);
    }
  }));
  if(monaco.languages.registerInlayHintsProvider){
    p1Providers.push(monaco.languages.registerInlayHintsProvider(language,{
      provideInlayHints:async(model,range,token)=>{
        const response=await lspRequest("textDocument/inlayHint",{
          textDocument:{uri:model.uri.toString()},
          range:{
            start:{line:range.startLineNumber-1,character:range.startColumn-1},
            end:{line:range.endLineNumber-1,character:range.endColumn-1},
          },
        },language,model);
        if(token?.isCancellationRequested||ed.getModel()!==model)return {hints:[]};
        return {hints:mapLspInlayHints(response?.result,monaco)};
      },
      resolveInlayHint:async hint=>hint,
    }));
  }
  if(monaco.languages.registerSignatureHelpProvider){
    p1Providers.push(monaco.languages.registerSignatureHelpProvider(language,{
      signatureHelpTriggerCharacters:["(",","],
      signatureHelpRetriggerCharacters:[")"],
      provideSignatureHelp:async(model,pos,token,context)=>{
        const response=await lspRequest("textDocument/signatureHelp",{
          textDocument:{uri:model.uri.toString()},
          position:{line:pos.lineNumber-1,character:pos.column-1},
          context:{triggerKind:context?.triggerKind??1,isRetrigger:context?.isRetrigger===true,...(context?.triggerCharacter?{triggerCharacter:context.triggerCharacter}:{})},
        },language,model);
        if(token?.isCancellationRequested||ed.getModel()!==model)return null;
        return mapLspSignatureHelp(response?.result);
      },
    }));
  }
  p1Providers.push(monaco.languages.registerRenameProvider(language,{provideRenameEdits:async(model,pos,newName)=>{
    const ld=await lspRequest("textDocument/rename",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1},newName},language,model);
    return mapLspWorkspaceEdit(ld?.result,monaco);
  }}));
  p1Providers.push(monaco.languages.registerCompletionItemProvider(language,{triggerCharacters:[".","_"],provideCompletionItems:async(model,pos)=>{const l=lang(pathRef.current);const ld=await lspRequest("textDocument/completion",{textDocument:{uri:model.uri.toString()},position:{line:pos.lineNumber-1,character:pos.column-1}},l,model);const live=ld?.result?.items||(Array.isArray(ld?.result)?ld.result:[]);if(live.length)return {suggestions:live.slice(0,200).map(x=>mapLspCompletionItem(x,monaco,model,pos)).filter(Boolean)};const word=model.getWordUntilPosition(pos);const d=await request("completion",{query:word.word,line:pos.lineNumber},model);return {suggestions:(d.completions||[]).map(x=>({label:x.label,kind:monaco.languages.CompletionItemKind.Text,detail:x.detail,insertText:x.label,range:{startLineNumber:pos.lineNumber,startColumn:word.startColumn,endLineNumber:pos.lineNumber,endColumn:word.endColumn}}))};}}));
  });
 };
 registerP1();
 const p1Diagnostics=ed.createDecorationsCollection([]);
 applyLspDiagnostics=(payload)=>{
   const notes=payload?.notifications||[];
   for(const n of notes){
     if(n?.method!=="textDocument/publishDiagnostics")continue;
     const uri=n.params?.uri||"";
     const current=ed.getModel();
     if(!current||current.uri.toString()!==uri)continue;
     const incomingVersion=n.params?.version;
     const currentVersion=ed.__dosLspVersions?.get(uri);
     if(Number.isInteger(incomingVersion)&&Number.isInteger(currentVersion)&&incomingVersion<currentVersion)continue;
     const marks=mapLspDiagnostics(n.params?.diagnostics,monaco);
     monaco.editor.setModelMarkers(current,"lsp",marks);
   }
 };
 const refreshP1Diagnostics=async()=>{
   const requestWorkspaceId=workspaceIdRef.current;
   const requestPath=pathRef.current;
   const targetModel=ed.getModel();
   if(!requestWorkspaceId){if(targetModel)monaco.editor.setModelMarkers(targetModel,"developer-os-symbols",[]);return;}
   const d=await request("diagnostics",{});
   if(workspaceIdRef.current!==requestWorkspaceId||pathRef.current!==requestPath||!targetModel||ed.getModel()!==targetModel)return;
   const markers=(d.diagnostics||[]).filter(x=>x.path===requestPath).map(x=>({severity:x.severity==="error"?monaco.MarkerSeverity.Error:x.severity==="warning"?monaco.MarkerSeverity.Warning:monaco.MarkerSeverity.Info,message:x.message||"",startLineNumber:x.line||1,startColumn:x.column||1,endLineNumber:x.line||1,endColumn:(x.column||1)+1,source:x.source||"Developer OS",code:x.code||""}));
   monaco.editor.setModelMarkers(targetModel,"developer-os-symbols",markers);
   p1Diagnostics.set(markers.map(x=>({range:{startLineNumber:x.startLineNumber,startColumn:x.startColumn,endLineNumber:x.endLineNumber,endColumn:x.endColumn},options:{inlineClassName:"developer-os-diagnostic"}})));
 };
 ed.__dosRefreshP1Diagnostics=refreshP1Diagnostics;
 const p1Actions=ed.addAction({id:"developer-os.rename-preview",label:"Developer OS: Rename with Diff Preview",run:e=>{const m=e.getModel(),pos=e.getPosition();const word=m?.getWordAtPosition(pos);if(word)window.dispatchEvent(new CustomEvent("developer-os:rename-preview",{detail:{old:word.word,path:pathRef.current}}));}});
 const p1Refs=ed.addAction({id:"developer-os.references-panel",label:"Developer OS: Find References (Developer OS)",run:e=>{const m=e.getModel(),pos=e.getPosition();const word=m?.getWordAtPosition(pos);if(word)window.dispatchEvent(new CustomEvent("developer-os:find-references",{detail:{name:word.word,path:pathRef.current}}));}});


 const c=ed.onDidChangeModelContent(()=>{
   const changedModel=ed.getModel();
   const changedPath=pathRef.current;
   const changedWorkspaceId=workspaceIdRef.current;
   if(typeof change.current==="function")change.current(ed.getValue());
   clearTimeout(lspTimerRef.current);lspTimerRef.current=null;
   if(!changedModel||!changedWorkspaceId||!["python","javascript","typescript"].includes(lang(changedPath)))return;
   const changedUri=changedModel.uri.toString();
   pendingLspChange={model:changedModel,path:changedPath,workspaceId:changedWorkspaceId,uri:changedUri,language:lang(changedPath)};
   lspTimerRef.current=setTimeout(()=>{void flushPendingLspChange();},300);
 });
 const p=ed.onDidChangeCursorPosition(e=>{if(typeof cursor.current==="function")cursor.current({line:e.position.lineNumber,column:e.position.column,path:pathRef.current});});
 const formatRequest=async(pathName,content,requestWorkspaceId=workspaceIdRef.current)=>{
   if(!requestWorkspaceId||workspaceIdRef.current!==requestWorkspaceId)return null;
   const token=getAccessToken();
   try{
    const response=await fetch(API_URL+"/ide/workspaces/"+requestWorkspaceId+"/format/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({path:pathName,content})});
    if(!response.ok||workspaceIdRef.current!==requestWorkspaceId)return null;
    const data=await response.json().catch(()=>null);
    return workspaceIdRef.current===requestWorkspaceId?data:null;
   }catch{return null}
 };
 const codeActionsRequest=async(model,range)=>{
   const requestWorkspaceId=workspaceIdRef.current;
   const requestPath=pathRef.current;
   if(!requestWorkspaceId)return [];
   const token=getAccessToken();
   try{
    const response=await fetch(API_URL+"/ide/workspaces/"+requestWorkspaceId+"/symbols/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({action:"code_actions",path:requestPath,line:range.startLineNumber})});
    if(!response.ok||workspaceIdRef.current!==requestWorkspaceId||pathRef.current!==requestPath||ed.getModel()!==model)return [];
    const data=await response.json().catch(()=>({}));
    if(workspaceIdRef.current!==requestWorkspaceId||pathRef.current!==requestPath||ed.getModel()!==model)return [];
    return Array.isArray(data.actions)?data.actions:[];
   }catch{return []}
 };
 const codeActionProviders=["python","javascript","typescript"].map(language=>monaco.languages.registerCodeActionProvider(language,{
   provideCodeActions:async(model,range)=>{
     const actions=await codeActionsRequest(model,range);
     return {actions:actions.map((a,index)=>({title:a.title||a.description||("Code Action "+(index+1)),kind:a.kind||"quickfix",diagnostics:[],edit:a.edit?mapLspWorkspaceEdit(a.edit,monaco):undefined,command:a.command}))};
   }
 }));
 const formatProvider=monaco.languages.registerDocumentFormattingEditProvider("python",{provideDocumentFormattingEdits:async(model)=>{
   const requestWorkspaceId=workspaceIdRef.current;
   const requestPath=pathRef.current;
   const r=await formatRequest(requestPath,model.getValue(),requestWorkspaceId);
   if(workspaceIdRef.current!==requestWorkspaceId||pathRef.current!==requestPath||ed.getModel()!==model)return [];
   return r?.changed?[{range:model.getFullModelRange(),text:r.content}]:[];
 }});
 ["javascript","typescript","json","css","scss","html"].forEach(language=>{
   p1Providers.push(monaco.languages.registerDocumentFormattingEditProvider(language,{provideDocumentFormattingEdits:async(model)=>{
     const requestWorkspaceId=workspaceIdRef.current;
     const requestPath=pathRef.current;
     const r=await formatRequest(requestPath,model.getValue(),requestWorkspaceId);
     if(workspaceIdRef.current!==requestWorkspaceId||pathRef.current!==requestPath||ed.getModel()!==model)return [];
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
 const workspaceSymbolsAction=ed.addAction({id:"developer-os.workspace-symbols",label:"Developer OS: Workspace Symbols",keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyMod.Shift|monaco.KeyCode.KeyO],run:async e=>{const m=e.getModel();if(!m)return;let remote=[];try{const d=await request("symbols",{query:""},m);remote=Array.isArray(d?.symbols)?d.symbols:[]}catch(error){void error}if(ed.getModel()!==m)return;const local=workspaceSymbols(m.getValue()).map((s,index)=>({name:s.name,line:m.getPositionAt(s.offset).lineNumber,column:m.getPositionAt(s.offset).column,index,path:pathRef.current,kind:"local"}));const merged=(remote.length?remote:local).slice(0,500);if(typeof onWorkspaceSymbols==="function")onWorkspaceSymbols(merged.map((s,index)=>({name:s.name,line:Number(s.line||1),column:Number(s.column||1),index,path:s.path||pathRef.current,kind:s.kind||"symbol"})));window.dispatchEvent(new CustomEvent("developer-os:workspace-symbols",{detail:{path:pathRef.current,symbols:merged}}));e.focus();}});
  return()=>{clearTimeout(lspTimerRef.current);lspTimerRef.current=null;pendingLspChange=null;ed.__dosRefreshP1Diagnostics=null;ed.__dosLspVersions?.clear();ed.__dosLspVersions=null;if(workspaceIdRef.current&&ed.__dosLspOpened)for(const uri of ed.__dosLspOpened)void lspRequest("textDocument/didClose",{textDocument:{uri}});p1Providers.forEach(x=>x.dispose());codeActionProviders.forEach(x=>x.dispose());formatProvider.dispose();p1Actions.dispose();p1Refs.dispose();p1Diagnostics.clear();c.dispose();p.dispose();format.dispose();duplicate.dispose();explain.dispose();fix.dispose();completionAction.dispose();gotoLine.dispose();foldAll.dispose();unfoldAll.dispose();symbolOutline.dispose();focusBreadcrumb.dispose();renameSelection.dispose();references.dispose();openImport.dispose();workspaceSymbolsAction.dispose();for(const m of models.current.values())if(!m.isDisposed())m.dispose();models.current.clear();viewStates.current.clear();ed.dispose();editor.current=null;model.current=null}},[]);
 useEffect(()=>{const ed=editor.current;if(!ed||!editorSettings)return;const fontSize=Number(editorSettings.fontSize);ed.updateOptions({fontSize:Number.isFinite(fontSize)?Math.min(24,Math.max(11,Math.round(fontSize))):13,tabSize:editorSettings.tabSize===4?4:2,wordWrap:editorSettings.wordWrap===true?"on":"off",minimap:{enabled:editorSettings.minimap!==false,side:"right",showSlider:"mouseover",renderCharacters:false},lineNumbers:editorSettings.lineNumbers===false?"off":"on",stickyScroll:{enabled:editorSettings.stickyScroll!==false},formatOnType:editorSettings.formatOnType!==false,fontLigatures:editorSettings.fontLigatures===true, cursorSmoothCaretAnimation:editorSettings.smoothCaret!==false?"on":"off",renderWhitespace:["none","selection","all"].includes(editorSettings.renderWhitespace)?editorSettings.renderWhitespace:"selection",codeLens:editorSettings.codeLens!==false,smoothScrolling:editorSettings.smoothScrolling!==false,mouseWheelZoom:editorSettings.mouseWheelZoom===true,guides:{indentation:true,bracketPairs:editorSettings.bracketPairGuides!==false,highlightActiveBracketPair:editorSettings.bracketPairGuides!==false},renderLineHighlight:["none","gutter","line","all"].includes(editorSettings.renderLineHighlight)?editorSettings.renderLineHighlight:"line"});if(["developer-os-dark","vs-dark","vs","hc-black"].includes(editorSettings.theme))monaco.editor.setTheme(editorSettings.theme);ed.layout();},[editorSettings,editorReady]);
 useEffect(()=>{const ed=editor.current;if(!ed)return;const normalized=path||"untitled";const previous=pathRef.current;const workspaceChanged=modelWorkspaceRef.current!==workspaceId;if(previous&&ed.getModel()===model.current){viewStates.current.set(previous,ed.saveViewState());}if(workspaceChanged){const oldWorkspace=modelWorkspaceRef.current;if(oldWorkspace&&ed.__dosLspOpened){for(const uri of ed.__dosLspOpened){const token=getAccessToken();void fetch(API_URL+"/ide/workspaces/"+oldWorkspace+"/lsp/",{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify({language:lang(normalized),method:"textDocument/didClose",uri,params:{textDocument:{uri}}})}).catch(()=>{});}}ed.__dosLspOpened=new Set();ed.__dosLspVersions=new Map();modelWorkspaceRef.current=workspaceId;}pathRef.current=normalized;const uri=monaco.Uri.parse(`inmemory://developer-os/${encodeURIComponent(normalized)}`);let next=models.current.get(normalized);if(!next||next.isDisposed()){next=monaco.editor.getModel(uri)||monaco.editor.createModel(value||"",lang(normalized),uri);models.current.set(normalized,next);}else{monaco.editor.setModelLanguage(next,lang(normalized));}if(next.getValue()!==(value||"")&&(!ed.hasTextFocus()||workspaceChanged))next.setValue(value||"");if(ed.getModel()!==next)ed.setModel(next);model.current=next;
  if(workspaceIdRef.current&&["python","javascript","typescript"].includes(lang(normalized))){
    const uriString=next.uri.toString();
    if(!ed.__dosLspOpened)ed.__dosLspOpened=new Set();
    if(!ed.__dosLspOpened.has(uriString)){
      ed.__dosLspOpened.add(uriString);
      if(!ed.__dosLspVersions)ed.__dosLspVersions=new Map();
      ed.__dosLspVersions.set(uriString,1);
      void lspRequest("textDocument/didOpen",{textDocument:{uri:uriString,languageId:lang(normalized),version:1,text:next.getValue()}},lang(normalized),next);
    }
  }
  void ed.__dosRefreshP1Diagnostics?.();
  const saved=viewStates.current.get(normalized);if(saved&&!workspaceChanged)ed.restoreViewState(saved);ed.focus();return()=>{clearTimeout(lspTimerRef.current);lspTimerRef.current=null;if(model.current===next)model.current=null}},[path,workspaceId]);
 useEffect(()=>{const m=model.current;if(m&&m.getValue()!==(value||"")&&!editor.current?.hasTextFocus())m.setValue(value||"")},[value]);
 useEffect(()=>{const ed=editor.current;if(!ed)return;const reveal=e=>{const line=Math.max(1,Number(e.detail?.line||1));const column=Math.max(1,Number(e.detail?.column||1));ed.revealPositionInCenter({lineNumber:line,column});ed.setPosition({lineNumber:line,column});ed.focus()};const focusEditor=()=>{if(ed.getModel()){ed.focus();ed.revealPositionInCenter(ed.getPosition()||{lineNumber:1,column:1})}};window.addEventListener("developer-os:reveal-line",reveal);window.addEventListener("developer-os:focus-editor",focusEditor);return()=>{window.removeEventListener("developer-os:reveal-line",reveal);window.removeEventListener("developer-os:focus-editor",focusEditor)}},[path]);
 useEffect(()=>{const m=model.current;if(!m)return;const markers=(diagnostics||[]).filter(x=>!x.path||x.path===path).map(x=>({startLineNumber:Math.max(1,x.line||1),endLineNumber:Math.max(1,x.end_line||x.line||1),startColumn:Math.max(1,x.column||1),endColumn:Math.max(2,x.end_column||((x.column||1)+1)),message:x.message||"Diagnostic",severity:x.severity==="warning"?monaco.MarkerSeverity.Warning:x.severity==="info"?monaco.MarkerSeverity.Info:monaco.MarkerSeverity.Error}));monaco.editor.setModelMarkers(m,"developer-os",markers);return()=>monaco.editor.setModelMarkers(m,"developer-os",[])},[diagnostics,path]);

 useEffect(()=>{const ed=editor.current;if(!ed)return;const dec=(remoteCursors||[]).filter(x=>x.cursor?.path===path||x.cursor?.file===path).map(x=>{const line=Math.max(1,Number(x.cursor.line||1));const col=Math.max(1,Number(x.cursor.column||1));return {range:new monaco.Range(line,col,line,col),options:{className:"dos-remote-cursor",hoverMessage:{value:"**"+(x.username||"Developer")+"**"},afterContentClassName:"dos-remote-cursor-label",stickiness:monaco.editor.TrackedRangeStickiness.Never}}});remoteDecorations.current=ed.deltaDecorations(remoteDecorations.current,dec);return()=>{remoteDecorations.current=ed.deltaDecorations(remoteDecorations.current,[])}},[remoteCursors,path]);
 return <div ref={host} className="monaco-host" aria-label={`Developer OS code editor ${path||"untitled"}`}/>;
}


// Remote cursor decorations are lightweight Monaco decorations driven by collaboration presence.
