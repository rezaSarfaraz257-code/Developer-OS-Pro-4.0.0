// Developer OS Project Intelligence
// Deterministic workspace detector used before runtime, LSP and package operations.
import { detectLanguage, LANGUAGE_REGISTRY } from "./languageRegistry";

const MANIFESTS = Object.freeze({
  "package.json":["node"],"pnpm-lock.yaml":["node","pnpm"],"yarn.lock":["node","yarn"],
  "package-lock.json":["node","npm"],"requirements.txt":["python","pip"],"pyproject.toml":["python"],
  "Pipfile":["python","pipenv"],"poetry.lock":["python","poetry"],"go.mod":["go"],
  "Cargo.toml":["rust","cargo"],"pom.xml":["java","maven"],"build.gradle":["java","gradle"],
  "build.gradle.kts":["java","gradle"],"composer.json":["php","composer"],"Gemfile":["ruby","bundler"],
  "pubspec.yaml":["dart","pub"],"Package.swift":["swift","swiftpm"],"mix.exs":["elixir","mix"],
  "rebar.config":["erlang","rebar3"],"*.csproj":["dotnet"],"*.fsproj":["dotnet"],
});

const FRAMEWORK_RULES = Object.freeze([
  ["next","Next.js",["next"]],["react","React",["react","react-dom"]],["vite","Vite",["vite"]],
  ["angular","Angular",["@angular/core"]],["vue","Vue",["vue"]],["svelte","Svelte",["svelte"]],
  ["django","Django",["django"]],["fastapi","FastAPI",["fastapi"]],["flask","Flask",["flask"]],
  ["express","Express",["express"]],["nestjs","NestJS",["@nestjs/core"]],["spring","Spring",["spring-boot"]],
]);
const base=p=>String(p||"").split("/").pop()||"";

function npmDeps(files){
  try{const p=JSON.parse(files["package.json"]||"{}");return new Set(Object.keys({...p.dependencies,...p.devDependencies,...p.peerDependencies}));}
  catch{return new Set();}
}

export function detectProject(files={}){
  const paths=Object.keys(files), names=new Set(paths.map(base)), counts={};
  for(const path of paths){const id=detectLanguage(path);if(id!=="plaintext")counts[id]=(counts[id]||0)+1;}
  const deps=npmDeps(files), frameworks=[];
  for(const [id,label,need] of FRAMEWORK_RULES){
    const pySignal=(id==="django"&&paths.some(p=>base(p)==="manage.py"))||(id==="fastapi"&&paths.some(p=>/requirements\.txt$|pyproject\.toml$/.test(p)&&/fastapi/i.test(files[p]||"")));
    if(need.some(x=>deps.has(x))||pySignal)frameworks.push({id,label});
  }
  const manifests=[];
  for(const [name,managers] of Object.entries(MANIFESTS)){
    if(name.startsWith("*")?paths.some(p=>base(p).endsWith(name.slice(1))):names.has(name))manifests.push({name,managers});
  }
  const languages=Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([id,count])=>({id,label:LANGUAGE_REGISTRY[id]?.label||id,files:count}));
  const primaryLanguage=languages[0]?.id||"plaintext";
  let projectType="generic";
  if(frameworks.some(f=>["next","react","vite","angular","vue","svelte"].includes(f.id)))projectType="web";
  else if(frameworks.some(f=>["django","fastapi","flask"].includes(f.id)))projectType="backend";
  else if(manifests.some(m=>["go.mod","Cargo.toml","pom.xml"].includes(m.name)))projectType="application";
  const confidence=paths.length?Math.min(.99,.45+(languages[0]?.files||0)/Math.max(paths.length,1)*.45+frameworks.length*.05):0;
  return {projectType,primaryLanguage,languages,frameworks,manifests,confidence:Number(confidence.toFixed(2)),signals:{fileCount:paths.length}};
}

export function detectFileContext(path,files={}){const id=detectLanguage(path);return {language:id,profile:LANGUAGE_REGISTRY[id]||LANGUAGE_REGISTRY.plaintext,project:detectProject(files)};}
