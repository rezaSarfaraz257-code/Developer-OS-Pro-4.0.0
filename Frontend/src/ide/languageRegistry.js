// Developer OS Polyglot Language Registry
// Single source of truth for editor mode, runtime routing and IDE capabilities.
// Runtime availability is negotiated with the Runner; editor support remains local.

export const LANGUAGE_REGISTRY = Object.freeze({
  python:{label:"Python",extensions:["py"],monaco:"python",runtime:"python",runner:"python",packageManager:"pip"},
  javascript:{label:"JavaScript",extensions:["js","mjs","cjs"],monaco:"javascript",runtime:"node",runner:"node",packageManager:"npm"},
  typescript:{label:"TypeScript",extensions:["ts"],monaco:"typescript",runtime:"node",runner:"npx",packageManager:"npm"},
  jsx:{label:"React JSX",extensions:["jsx"],monaco:"javascript",runtime:"node",runner:"node",packageManager:"npm"},
  tsx:{label:"React TSX",extensions:["tsx"],monaco:"typescript",runtime:"node",runner:"npx",packageManager:"npm"},
  go:{label:"Go",extensions:["go"],monaco:"go",runtime:"go",runner:"go",packageManager:"go"},
  rust:{label:"Rust",extensions:["rs"],monaco:"rust",runtime:"rustc",runner:"rustc",packageManager:"cargo"},
  java:{label:"Java",extensions:["java"],monaco:"java",runtime:"java",runner:"javac",packageManager:"maven"},
  kotlin:{label:"Kotlin",extensions:["kt","kts"],monaco:"kotlin",runtime:"kotlin",runner:"kotlinc",packageManager:"gradle"},
  c:{label:"C",extensions:["c","h"],monaco:"c",runtime:"gcc",runner:"gcc"},
  cpp:{label:"C++",extensions:["cc","cpp","cxx","hpp"],monaco:"cpp",runtime:"g++",runner:"g++"},
  csharp:{label:"C#",extensions:["cs"],monaco:"csharp",runtime:"dotnet",runner:"dotnet",packageManager:"dotnet"},
  php:{label:"PHP",extensions:["php"],monaco:"php",runtime:"php",runner:"php",packageManager:"composer"},
  ruby:{label:"Ruby",extensions:["rb"],monaco:"ruby",runtime:"ruby",runner:"ruby",packageManager:"gem"},
  perl:{label:"Perl",extensions:["pl","pm"],monaco:"perl",runtime:"perl",runner:"perl",packageManager:"cpan"},
  shell:{label:"Shell",extensions:["sh","bash","zsh"],monaco:"shell",runtime:"bash",runner:"bash"},
  lua:{label:"Lua",extensions:["lua"],monaco:"lua",runtime:"lua",runner:"lua",packageManager:"luarocks"},
  r:{label:"R",extensions:["r"],monaco:"r",runtime:"r",runner:"Rscript",packageManager:"cran"},
  dart:{label:"Dart",extensions:["dart"],monaco:"dart",runtime:"dart",runner:"dart",packageManager:"pub"},
  swift:{label:"Swift",extensions:["swift"],monaco:"swift",runtime:"swift",runner:"swift",packageManager:"swiftpm"},
  elixir:{label:"Elixir",extensions:["ex","exs"],monaco:"elixir",runtime:"elixir",runner:"elixir",packageManager:"mix"},
  erlang:{label:"Erlang",extensions:["erl","hrl"],monaco:"plaintext",runtime:"erl",runner:"escript",packageManager:"rebar3"},
  fsharp:{label:"F#",extensions:["fs","fsx"],monaco:"plaintext",runtime:"dotnet",runner:"dotnet",packageManager:"dotnet"},
  html:{label:"HTML",extensions:["html","htm"],monaco:"html"},
  css:{label:"CSS",extensions:["css"],monaco:"css"},
  scss:{label:"SCSS",extensions:["scss"],monaco:"scss",runtime:"node",runner:"npx",packageManager:"npm"},
  less:{label:"Less",extensions:["less"],monaco:"less",runtime:"node",runner:"npx",packageManager:"npm"},
  json:{label:"JSON",extensions:["json","jsonc"],monaco:"json"},
  yaml:{label:"YAML",extensions:["yml","yaml"],monaco:"yaml"},
  toml:{label:"TOML",extensions:["toml"],monaco:"plaintext"},
  markdown:{label:"Markdown",extensions:["md","markdown","mdx"],monaco:"markdown"},
  sql:{label:"SQL",extensions:["sql"],monaco:"sql"},
  graphql:{label:"GraphQL",extensions:["graphql","gql"],monaco:"graphql"},
  xml:{label:"XML",extensions:["xml"],monaco:"xml"},
  dockerfile:{label:"Dockerfile",extensions:["dockerfile"],monaco:"dockerfile"},
  plaintext:{label:"Plain Text",extensions:["txt","env"],monaco:"plaintext"},
});

const EXTENSION_INDEX = Object.freeze(Object.entries(LANGUAGE_REGISTRY).reduce((out,[id,profile])=>{
  profile.extensions.forEach(ext=>{out[ext.toLowerCase()]=id;});
  return out;
},{}));

export function detectLanguage(path=""){
  const name=String(path).split("/").pop()?.toLowerCase()||"";
  if(name==="dockerfile") return "dockerfile";
  return EXTENSION_INDEX[name.split(".").pop()]||"plaintext";
}

export function languageProfile(path){
  return LANGUAGE_REGISTRY[detectLanguage(path)]||LANGUAGE_REGISTRY.plaintext;
}

export function runtimeFor(path){
  return languageProfile(path).runtime||null;
}

export function isRunnable(path, capabilities){
  const runtime=runtimeFor(path);
  if(!runtime) return false;
  return Boolean(capabilities?.runtimes?.[runtime]?.available);
}

export function runnableLanguages(capabilities){
  return Object.entries(LANGUAGE_REGISTRY).filter(([,p])=>!p.runtime||capabilities?.runtimes?.[p.runtime]?.available);
}
