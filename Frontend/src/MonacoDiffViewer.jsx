import { useEffect, useRef } from "react";
import * as monaco from "monaco-editor";

export default function MonacoDiffViewer({ original="", modified="", path="" }){
  const host=useRef(null);
  const diff=useRef(null);
  const models=useRef([]);
  useEffect(()=>{
    if(!host.current)return;
    const editor=monaco.editor.createDiffEditor(host.current,{
      theme:"developer-os-dark",
      automaticLayout:true,
      readOnly:true,
      renderSideBySide:true,
      originalEditable:false,
      minimap:{enabled:false},
      fontFamily:"JetBrains Mono, Consolas, monospace",
      fontSize:12,
      lineHeight:20,
      scrollBeyondLastLine:false,
      padding:{top:8,bottom:12},
      renderOverviewRuler:true,
      ignoreTrimWhitespace:false,
      diffAlgorithm:"advanced",
    });
    diff.current=editor;
    const language=(p)=>{
      const e=p.split(".").pop()?.toLowerCase();
      return {js:"javascript",jsx:"javascript",ts:"typescript",tsx:"typescript",json:"json",py:"python",html:"html",css:"css",scss:"scss",md:"markdown",yaml:"yaml",yml:"yaml",sql:"sql",sh:"shell",go:"go",rs:"rust",java:"java",php:"php"}[e]||"plaintext";
    };
    const uriBase="inmemory://developer-os-ai-diff/";
    const left=monaco.editor.createModel(original||"",language(path),monaco.Uri.parse(uriBase+"original/"+encodeURIComponent(path||"untitled")));
    const right=monaco.editor.createModel(modified||"",language(path),monaco.Uri.parse(uriBase+"modified/"+encodeURIComponent(path||"untitled")));
    models.current=[left,right];
    editor.setModel({original:left,modified:right});
    return()=>{editor.dispose();models.current.forEach(m=>m.dispose());models.current=[];diff.current=null};
  },[]);
  useEffect(()=>{
    const [left,right]=models.current;
    if(left&&left.getValue()!==String(original||""))left.setValue(String(original||""));
    if(right&&right.getValue()!==String(modified||""))right.setValue(String(modified||""));
  },[original,modified]);
  return <div ref={host} className="dos-monaco-diff" aria-label={"AI diff "+(path||"file")}/>;
}
