import test from "node:test";
import assert from "node:assert/strict";
import { createLspDocumentQueue } from "../src/ide/lspDocumentQueue.js";

test("LSP lifecycle notifications are serialized for the same document", async () => {
  const queue = createLspDocumentQueue();
  const order = [];
  let releaseOpen;
  const opened = new Promise((resolve) => { releaseOpen = resolve; });

  const didOpen = queue.enqueue("workspace-1:file:///a.py", async () => {
    order.push("didOpen:start");
    await opened;
    order.push("didOpen:end");
  });
  const didChange = queue.enqueue("workspace-1:file:///a.py", async () => {
    order.push("didChange");
  });

  await Promise.resolve();
  assert.deepEqual(order, ["didOpen:start"]);
  releaseOpen();
  await Promise.all([didOpen, didChange]);
  assert.deepEqual(order, ["didOpen:start", "didOpen:end", "didChange"]);
});

test("LSP queue failures do not poison later notifications", async () => {
  const queue = createLspDocumentQueue();
  const order = [];
  await assert.rejects(queue.enqueue("doc", async () => {
    order.push("failed");
    throw new Error("temporary transport failure");
  }), /temporary transport failure/);
  await queue.enqueue("doc", async () => order.push("retry"));
  assert.deepEqual(order, ["failed", "retry"]);
});

test("LSP queue isolates different documents and flush waits only for its own document", async () => {
  const queue = createLspDocumentQueue();
  let releaseA;
  const holdA = new Promise((resolve) => { releaseA = resolve; });
  const a = queue.enqueue("workspace-1:a.py", () => holdA);
  const b = queue.enqueue("workspace-1:b.py", async () => "ready");

  assert.equal(await b, "ready");
  let flushedA = false;
  const waitingA = queue.flush("workspace-1:a.py").then(() => { flushedA = true; });
  await Promise.resolve();
  assert.equal(flushedA, false);
  releaseA();
  await Promise.all([a, waitingA]);
  assert.equal(flushedA, true);
  await queue.flush("workspace-1:b.py");
});


test("LSP queue isolates identical URIs across workspaces", async () => {
  const queue = createLspDocumentQueue();
  let releaseFirst;
  const firstGate = new Promise(resolve => { releaseFirst = resolve; });
  const first = queue.enqueue("workspace-10:file:///shared.py", () => firstGate);
  const second = queue.enqueue("workspace-11:file:///shared.py", async () => "independent");

  assert.equal(await second, "independent");
  releaseFirst();
  await first;
});

test("LSP queue recovers after a synchronous exception and preserves later FIFO work", async () => {
  const queue = createLspDocumentQueue();
  const order = [];
  const failed = queue.enqueue("workspace:file:///sync.py", () => {
    order.push("throws");
    throw new TypeError("synchronous provider failure");
  });
  const recovered = queue.enqueue("workspace:file:///sync.py", () => {
    order.push("recovered");
    return "ok";
  });

  await assert.rejects(failed, /synchronous provider failure/);
  assert.equal(await recovered, "ok");
  assert.deepEqual(order, ["throws", "recovered"]);
});

test("flush does not wait for work added later after its snapshot has settled", async () => {
  const queue = createLspDocumentQueue();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const first = queue.enqueue("doc", () => gate);
  let flushed = false;
  const waiting = queue.flush("doc").then(() => { flushed = true; });

  release();
  await Promise.all([first, waiting]);
  assert.equal(flushed, true);

  let releaseLater;
  const laterGate = new Promise(resolve => { releaseLater = resolve; });
  const later = queue.enqueue("doc", () => laterGate);
  await queue.flush("doc");
  releaseLater();
  await later;
});
