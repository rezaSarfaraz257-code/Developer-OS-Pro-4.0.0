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


// LSP capability routing. This is intentionally declarative: actual language-server
// processes are provisioned by the future backend orchestrator, while the IDE can
// already make deterministic routing decisions without pretending a server exists.
export const LSP_PROFILES = Object.freeze({
  javascript:{server:"typescript-language-server",languageId:"javascript",transport:"stdio",priority:"core"},
  typescript:{server:"typescript-language-server",languageId:"typescript",transport:"stdio",priority:"core"},
  jsx:{server:"typescript-language-server",languageId:"javascriptreact",transport:"stdio",priority:"core"},
  tsx:{server:"typescript-language-server",languageId:"typescriptreact",transport:"stdio",priority:"core"},
  python:{server:"pyright-langserver",languageId:"python",transport:"stdio",priority:"core"},
  go:{server:"gopls",languageId:"go",transport:"stdio",priority:"core"},
  rust:{server:"rust-analyzer",languageId:"rust",transport:"stdio",priority:"core"},
  c:{server:"clangd",languageId:"c",transport:"stdio",priority:"extended"},
  cpp:{server:"clangd",languageId:"cpp",transport:"stdio",priority:"extended"},
  java:{server:"jdtls",languageId:"java",transport:"stdio",priority:"extended"},
  php:{server:"intelephense",languageId:"php",transport:"stdio",priority:"extended"},
  ruby:{server:"ruby-lsp",languageId:"ruby",transport:"stdio",priority:"extended"},
  csharp:{server:"OmniSharp",languageId:"csharp",transport:"stdio",priority:"extended"},
});

export const PACKAGE_PROFILES = Object.freeze({
  npm:{manager:"npm",manifest:["package.json"],lockfiles:["package-lock.json"]},
  pnpm:{manager:"pnpm",manifest:["package.json"],lockfiles:["pnpm-lock.yaml"]},
  yarn:{manager:"yarn",manifest:["package.json"],lockfiles:["yarn.lock"]},
  pip:{manager:"pip",manifest:["requirements.txt","pyproject.toml"],lockfiles:["requirements.txt","poetry.lock"]},
  cargo:{manager:"cargo",manifest:["Cargo.toml"],lockfiles:["Cargo.lock"]},
  go:{manager:"go",manifest:["go.mod"],lockfiles:["go.sum"]},
  maven:{manager:"maven",manifest:["pom.xml"],lockfiles:[]},
  gradle:{manager:"gradle",manifest:["build.gradle","build.gradle.kts"],lockfiles:[]},
  composer:{manager:"composer",manifest:["composer.json"],lockfiles:["composer.lock"]},
  bundler:{manager:"bundler",manifest:["Gemfile"],lockfiles:["Gemfile.lock"]},
  pub:{manager:"dart pub",manifest:["pubspec.yaml"],lockfiles:["pubspec.lock"]},
  swiftpm:{manager:"swift package manager",manifest:["Package.swift"],lockfiles:["Package.resolved"]},
  mix:{manager:"mix",manifest:["mix.exs"],lockfiles:["mix.lock"]},
  rebar3:{manager:"rebar3",manifest:["rebar.config"],lockfiles:[]},
  dotnet:{manager:"dotnet",manifest:["*.csproj","*.fsproj"],lockfiles:["packages.lock.json"]},
});

export function detectPackageManager(files={}){
  const paths=Object.keys(files), names=new Set(paths.map(base));
  const candidates=[];
  for(const [id,p] of Object.entries(PACKAGE_PROFILES)){
    const matched=p.manifest.some(m=>m.startsWith("*")?paths.some(x=>base(x).endsWith(m.slice(1))):names.has(m));
    if(matched)candidates.push({id,...p,lockfile:p.lockfiles.some(x=>names.has(x))});
  }
  const priority=["pnpm","yarn","npm","poetry","pip","cargo","go","maven","gradle","composer","bundler","pub","swiftpm","mix","rebar3","dotnet"];
  candidates.sort((a,b)=>Number(b.lockfile)-Number(a.lockfile)||priority.indexOf(a.id)-priority.indexOf(b.id));
  return candidates[0]||null;
}

export function lspProfileForLanguage(language){
  return LSP_PROFILES[language] || null;
}

export function buildLSPPlan(files={}){
  const project=detectProject(files);
  const plans=[];
  for(const entry of project.languages){
    const profile=lspProfileForLanguage(entry.id);
    if(!profile) continue;
    plans.push({
      language:entry.id,
      files:entry.files,
      server:profile.server,
      languageId:profile.languageId,
      transport:profile.transport,
      priority:profile.priority,
      workspaceRoot:".",
      state:"planned",
    });
  }
  return {
    workspaceRoot:".",
    projectType:project.projectType,
    framework:project.frameworks[0]?.id||null,
    servers:plans,
    strategy:plans.length>1?"multi-server":"single-server",
    lifecycle:"on-demand",
  };
}
