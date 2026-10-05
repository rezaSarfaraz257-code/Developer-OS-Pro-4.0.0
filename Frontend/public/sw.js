const CACHE = "developer-os-runtime-v1";
const OUTBOX_TAG = "developer-os-outbox-sync";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("message", (event) => {
  if (event.data?.type !== "REGISTER_OUTBOX_SYNC") return;
  event.waitUntil?.(registerSync());
});

async function registerSync() {
  if (!self.registration.sync) return false;
  try {
    await self.registration.sync.register(OUTBOX_TAG);
    return true;
  } catch {
    return false;
  }
}

self.addEventListener("sync", (event) => {
  if (event.tag === OUTBOX_TAG) {
    event.waitUntil(notifyClientsToReplay());
  }
});

async function notifyClientsToReplay() {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  await Promise.all(clients.map((client) => client.postMessage({
    type: "OUTBOX_SYNC_REQUEST",
    source: "service-worker",
  })));
}
