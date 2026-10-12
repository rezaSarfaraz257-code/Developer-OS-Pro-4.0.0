import test from "node:test";
import assert from "node:assert/strict";
import { buildReplacementPreview, findStaleReplacementChanges } from "../src/ide/replacePreview.js";

test("literal preview escapes regex metacharacters",()=>{
 const changes=buildReplacementPreview({files:{"src/a.js":"const x = a.b; a.b;"},query:"a.b",replacement:"value",replaceAll:true});
 assert.equal(changes.length,1);assert.equal(changes[0].after,"const x = value; value;");assert.equal(changes[0].matches,2);
});

test("single replacement previews only the selected match",()=>{
 const changes=buildReplacementPreview({files:{"a.txt":"one two one"},query:"one",replacement:"ONE",firstMatch:{path:"a.txt",line:1,column:1,match:"one"}});
 assert.equal(changes[0].after,"ONE two one");assert.equal(changes[0].matches,1);
});

test("replace all previews only matching files and honors case sensitivity",()=>{
 const changes=buildReplacementPreview({files:{"a.txt":"Foo foo","b.txt":"bar"},query:"foo",replacement:"x",replaceAll:true,caseSensitive:true});
 assert.equal(changes.length,1);assert.equal(changes[0].path,"a.txt");assert.equal(changes[0].after,"Foo x");
});

test("invalid regular expressions are rejected",()=>{
 assert.throws(()=>buildReplacementPreview({files:{"a.txt":"x"},query:"(",replacement:"y",regex:true,replaceAll:true}),SyntaxError);
});

test("stale selected match produces no unsafe replacement",()=>{
 const changes=buildReplacementPreview({files:{"a.txt":"changed"},query:"old",replacement:"new",firstMatch:{path:"a.txt",line:1,column:1,match:"old"}});
 assert.deepEqual(changes,[]);
});

test("stale preview preflight detects changed target files before writes",()=>{
 const changes=[{path:"a.txt",before:"old",after:"new"},{path:"b.txt",before:"stable",after:"changed"}];
 assert.deepEqual(findStaleReplacementChanges(changes,{"a.txt":"newer","b.txt":"stable"}),[changes[0]]);
});

test("stale preview preflight accepts unchanged target files",()=>{
 const changes=[{path:"a.txt",before:"old",after:"new"}];
 assert.deepEqual(findStaleReplacementChanges(changes,{"a.txt":"old"}),[]);
});
