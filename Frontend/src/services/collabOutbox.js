const DB_NAME = "developer-os-collaboration";
const STORE = "operations";
const VERSION = 1;

function openDb() {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
        store.createIndex("workspace_client", ["workspace_id", "client_id"]);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB unavailable"));
  });
}

export async function enqueueOutbox(item) {
  const db = await openDb();
  if (!db) return false;
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).add({ ...item, queued_at: Date.now() });
    tx.oncomplete = () => { db.close(); resolve(true); };
    tx.onerror = () => { db.close(); reject(tx.error || new Error("Outbox write failed")); };
  });
}

export async function listOutbox(workspaceId, clientId) {
  const db = await openDb();
  if (!db) return [];
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const request = tx.objectStore(STORE).index("workspace_client").getAll([String(workspaceId), String(clientId)]);
    request.onsuccess = () => resolve((request.result || []).sort((a, b) => a.id - b.id));
    request.onerror = () => reject(request.error || new Error("Outbox read failed"));
    tx.oncomplete = () => db.close();
  });
}

export async function removeOutbox(id) {
  const db = await openDb();
  if (!db) return;
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(id);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error || new Error("Outbox delete failed")); };
  });
}

export async function outboxCount(workspaceId, clientId) {
  return (await listOutbox(workspaceId, clientId)).length;
}
