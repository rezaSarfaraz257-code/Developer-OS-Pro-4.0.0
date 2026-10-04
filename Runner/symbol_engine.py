"""Safe workspace symbol intelligence for the IDE.

The index is static-analysis only: it never imports or executes workspace code.
Python uses the stdlib AST; JS/TS uses conservative lexical extraction.
"""
from __future__ import annotations
import ast,re
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
        kind="function" if text[m.start():].startswith("function") else "class" if text[m.start():].startswith("class") else "variable"
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
