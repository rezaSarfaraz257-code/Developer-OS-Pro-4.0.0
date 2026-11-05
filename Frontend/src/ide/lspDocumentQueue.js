/**
 * Serialize LSP document lifecycle notifications per workspace/document.
 * Feature requests can await flush() without serializing unrelated documents.
 */
export function createLspDocumentQueue() {
  const tails = new Map();

  function enqueue(key, operation) {
    if (typeof operation !== "function") {
      return Promise.reject(new TypeError("LSP queue operation must be a function"));
    }
    const previous = tails.get(key) || Promise.resolve();
    const current = previous.then(operation);
    let tail;
    tail = current.then(() => undefined, () => undefined).finally(() => {
      if (tails.get(key) === tail) tails.delete(key);
    });
    tails.set(key, tail);
    return current;
  }

  function flush(key) {
    return tails.get(key) || Promise.resolve();
  }

  return { enqueue, flush };
}
