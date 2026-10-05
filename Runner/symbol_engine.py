"""Safe workspace symbol intelligence for the IDE.

The index is static-analysis only: it never imports or executes workspace code.
Python uses the stdlib AST; JS/TS uses conservative lexical extraction.
"""
from __future__ import annotations
import ast,re
import difflib
from pathlib import Path

IGNORED={".git","node_modules",".venv","venv","dist","build","__pycache__"}
EXTS={".py",".js",".jsx",".ts",".tsx",".json"}

def _files(root):
    root=Path(root).resolve()
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXTS and not any(x in IGNORED for x in p.parts):
            yield p

def _rel(root,p): return str(p.resolve().relative_to(Path(root).resolve())).replace("\\","/")

def _python_symbols(path, text, rel):
    out=[]
    try: tree=ast.parse(text, filename=rel)
    except SyntaxError as e:
        return [{"name":"","kind":"diagnostic","path":rel,"line":e.lineno or 1,"column":e.offset or 1,"message":e.msg}]
    for n in ast.walk(tree):
        kind=None
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): kind="function"
        elif isinstance(n,ast.ClassDef): kind="class"
        elif isinstance(n,(ast.Assign,ast.AnnAssign)):
            targets=n.targets if isinstance(n,ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t,ast.Name): out.append({"name":t.id,"kind":"variable","path":rel,"line":t.lineno,"column":t.col_offset+1})
            continue
        if kind and getattr(n,"name",None):
            out.append({"name":n.name,"kind":kind,"path":rel,"line":n.lineno,"column":n.col_offset+1})
    return out

JS_DECL=re.compile(r"\b(?:function|class|interface|type|enum|const|let|var)\s+([A-Za-z_$][\w$]*)")
def _js_symbols(text,rel):
    out=[]
    for m in JS_DECL.finditer(text):
        line=text.count("\n",0,m.start())+1; col=m.start()-text.rfind("\n",0,m.start())
        kind="function" if text[m.start():].startswith("function") or (m.group(0).startswith(("const ","let ","var ")) and re.match(r"\s*=>", text[m.end():])) else "class" if text[m.start():].startswith("class") else "variable"
        out.append({"name":m.group(1),"kind":kind,"path":rel,"line":line,"column":col})
    return out

def index(root, query="", path=""):
    root=Path(root).resolve(); q=str(query or "").lower(); result=[]
    for p in _files(root):
        rel=_rel(root,p)
        if path and rel!=path: continue
        try:text=p.read_text(encoding="utf-8")
        except (OSError,UnicodeDecodeError): continue
        syms=_python_symbols(p,text,rel) if p.suffix==".py" else _js_symbols(text,rel)
        result.extend(s for s in syms if not q or q in s["name"].lower())
    return result[:2000]

def references(root,name,path=""):
    root=Path(root).resolve(); name=str(name or "").strip()
    if not name or len(name)>200: return []
    rx=re.compile(r"(?<![\w$])"+re.escape(name)+r"(?![\w$])")
    out=[]
    for p in _files(root):
        rel=_rel(root,p)
        if path and rel!=path: continue
        try:text=p.read_text(encoding="utf-8")
        except (OSError,UnicodeDecodeError): continue
        for i,line in enumerate(text.splitlines(),1):
            for m in rx.finditer(line):
                out.append({"name":name,"path":rel,"line":i,"column":m.start()+1,"kind":"reference","text":line.strip()[:500]})
                if len(out)>=2000:return out
    return out

def rename_preview(root,old,new,path=""):
    if not re.fullmatch(r"[A-Za-z_$][\w$]*",str(new or "")): raise ValueError("Invalid symbol name")
    refs=references(root,old,path)
    files={}
    root=Path(root).resolve()
    rx=re.compile(r"(?<![\w$])"+re.escape(old)+r"(?![\w$])")
    for ref in refs:
        p=root/ref["path"]
        if p not in files:
            try:files[p]=p.read_text(encoding="utf-8")
            except (OSError,UnicodeDecodeError):continue
    changes=[]
    for p,text in files.items():
        updated=rx.sub(new,text)
        if updated!=text:
            changes.append({"path":_rel(root,p),"content":updated,"matches":len(rx.findall(text))})
    return {"old":old,"new":new,"references":len(refs),"changes":changes}

def definitions(root, name, path=""):
    """Return static definitions without importing or executing workspace code."""
    name=str(name or "").strip()
    if not name or len(name)>200:
        return []
    return [s for s in index(root, query=name, path=path) if s.get("name")==name and s.get("kind") in {"function","class","variable"}][:100]

def hover(root, name, path="", line=0):
    name=str(name or "").strip()
    defs=definitions(root,name,path)
    if not defs:
        return None
    item=defs[0]; root=Path(root).resolve(); p=root/item["path"]
    try: lines=p.read_text(encoding="utf-8").splitlines()
    except (OSError,UnicodeDecodeError): lines=[]
    source_line=lines[item.get("line",1)-1].strip() if lines and item.get("line") else name
    return {"name":name,"kind":item.get("kind","symbol"),"path":item.get("path"),"line":item.get("line",1),"column":item.get("column",1),"signature":source_line[:500],"markdown":"**"+item.get("kind","symbol")+"** "+name+"\n\n"+source_line[:500]+"\n\nDefined in "+item.get("path","")+":"+str(item.get("line",1))}

def completion(root, query="", path=""):
    q=str(query or "").strip().lower(); seen=set(); out=[]
    keywords=["const","let","var","function","class","return","import","from","export","async","await","if","else","for","while","try","catch","throw","def","None","True","False","and","or","not","in","is","with","as"]
    for word in keywords:
        if not q or word.lower().startswith(q): seen.add(word); out.append({"label":word,"kind":"keyword","detail":"language keyword"})
    for s in index(root,path=path):
        n=s.get("name","")
        if n in seen or (q and not n.lower().startswith(q)): continue
        seen.add(n); out.append({"label":n,"kind":s.get("kind","variable"),"detail":s.get("kind","symbol")+" · "+s.get("path",""),"line":s.get("line",1)})
        if len(out)>=200: break
    return out[:200]

def rename_diff(root, old, new, path=""):
    preview=rename_preview(root, old, new, path)
    root=Path(root).resolve()
    for change in preview["changes"]:
        target=root/change["path"]
        try:
            before=target.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError,UnicodeDecodeError):
            before=[]
        after=change["content"].splitlines(keepends=True)
        change["diff"]="".join(difflib.unified_diff(before, after, fromfile=change["path"], tofile=change["path"]))
    return preview


def code_actions(root, path="", line=0):
    root=Path(root).resolve()
    actions=[]
    if path:
        try:
            text=(root/safe_rel_local(path)).read_text(encoding="utf-8")
        except Exception:
            text=""
        if path.endswith(".py"):
            try:
                ast.parse(text, filename=path)
            except SyntaxError as exc:
                actions.append({"title":"Review Python syntax error","kind":"quickfix","diagnostic":{"message":exc.msg,"line":exc.lineno or 1,"column":exc.offset or 1}})
    return actions[:50]


def safe_rel_local(path):
    raw=str(path).replace("\\","/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        raise ValueError("unsafe path")
    return raw


def diagnostics(root, path=""):
    """Static diagnostics: syntax errors plus conservative undefined-name checks."""
    root=Path(root).resolve()
    results=[]
    targets=[]
    for p in _files(root):
        rel=_rel(root,p)
        if path and rel != path:
            continue
        targets.append((p,rel))
    for p,rel in targets:
        try: text=p.read_text(encoding="utf-8")
        except (OSError,UnicodeDecodeError): continue
        if p.suffix == ".py":
            try:
                tree=ast.parse(text, filename=rel)
            except SyntaxError as exc:
                results.append({"severity":"error","code":"PY001","message":exc.msg,"path":rel,"line":exc.lineno or 1,"column":exc.offset or 1,"source":"static"})
                continue
            defined=set()
            imported=set()
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                    defined.add(node.name)
                    for arg in node.args.args: defined.add(arg.arg)
                elif isinstance(node,ast.Import):
                    for n in node.names: imported.add(n.asname or n.name.split(".")[0])
                elif isinstance(node,ast.ImportFrom):
                    for n in node.names: imported.add(n.asname or n.name)
                elif isinstance(node,ast.Name) and isinstance(node.ctx,ast.Store):
                    defined.add(node.id)
            builtins=set(dir(__builtins__)) if isinstance(__builtins__,dict) else set(dir(__builtins__))
            for node in ast.walk(tree):
                if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and node.id not in defined|imported|builtins:
                    results.append({"severity":"warning","code":"PY002","message":f"Possibly undefined name: {node.id}","path":rel,"line":node.lineno,"column":node.col_offset+1,"source":"static"})
                    if len(results)>=500: return results
        elif p.suffix in {".js",".jsx",".ts",".tsx"}:
            for i,line in enumerate(text.splitlines(),1):
                if re.search(r"(^|[;,{]\\s*)(const|let|var)\\s+[^=;]+;$",line):
                    continue
                if re.search(r"\\b(console\\.log|debugger)\\b",line):
                    results.append({"severity":"info","code":"JS001","message":"Debug statement found.","path":rel,"line":i,"column":max(1,line.find("console")+1),"source":"static"})
    return results[:500]
