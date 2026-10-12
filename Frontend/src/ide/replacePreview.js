function escapeRegex(value){return String(value??"").split("").map(char=>"\\^$.*+?()[]{}|".includes(char)?"\\"+char:char).join("");}

export function buildReplacementPreview({files={},query,replacement="",regex=false,caseSensitive=false,replaceAll=false,firstMatch=null}){
  if(!String(query??""))return [];
  const source=regex?String(query):escapeRegex(query);
  const flags=caseSensitive?"g":"gi";
  const pattern=new RegExp(source,flags);
  const paths=replaceAll?Object.keys(files).filter(path=>new RegExp(source,caseSensitive?"":"i").test(String(files[path]??""))):[firstMatch?.path].filter(Boolean);
  const changes=[];
  for(const path of [...new Set(paths)]){
    const before=String(files[path]??"");
    let after=before;
    let matches=0;
    if(replaceAll){
      matches=(before.match(pattern)||[]).length;
      after=before.replace(pattern,replacement);
    }else{
      const match=firstMatch;
      if(!match||match.path!==path)continue;
      const lines=before.split("\n");
      const lineIndex=Math.max(0,(match.line||1)-1);
      const columnIndex=Math.max(0,(match.column||1)-1);
      const line=lines[lineIndex]??"";
      const matchedText=String(match.match??"");
      if(!matchedText||line.slice(columnIndex,columnIndex+matchedText.length)!==matchedText)continue;
      const single=new RegExp(source,caseSensitive?"":"i");
      lines[lineIndex]=line.slice(0,columnIndex)+matchedText.replace(single,replacement)+line.slice(columnIndex+matchedText.length);
      after=lines.join("\n");matches=1;
    }
    if(after!==before)changes.push({path,before,after,matches});
  }
  return changes;
}
