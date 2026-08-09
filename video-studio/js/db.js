// IndexedDB store for jobs (survive a reload mid-generation) and clips (video Blobs).
//
// Video blobs are far too big for localStorage, and a generation can take minutes —
// long enough that a refresh mid-run is normal. Both are persisted so the queue can
// re-attach to in-flight provider operations on the next page load.

const DB_NAME = 'video-studio';
const DB_VERSION = 1;

let dbPromise = null;

function open() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('jobs')) {
        const jobs = db.createObjectStore('jobs', { keyPath: 'id' });
        jobs.createIndex('status', 'status');
        jobs.createIndex('createdAt', 'createdAt');
      }
      if (!db.objectStoreNames.contains('clips')) {
        const clips = db.createObjectStore('clips', { keyPath: 'id' });
        clips.createIndex('createdAt', 'createdAt');
        clips.createIndex('jobId', 'jobId');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

async function tx(store, mode, fn) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error ?? new Error('Transaction aborted'));
    if (req) {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    } else {
      t.oncomplete = () => resolve();
    }
  });
}

export const putJob = (job) => tx('jobs', 'readwrite', (s) => s.put(job));
export const deleteJob = (id) => tx('jobs', 'readwrite', (s) => s.delete(id));
export const allJobs = () => tx('jobs', 'readonly', (s) => s.getAll()).then(sortByCreated);

export const putClip = (clip) => tx('clips', 'readwrite', (s) => s.put(clip));
export const getClip = (id) => tx('clips', 'readonly', (s) => s.get(id));
export const deleteClip = (id) => tx('clips', 'readwrite', (s) => s.delete(id));
export const allClips = () => tx('clips', 'readonly', (s) => s.getAll()).then(sortByCreated).then((a) => a.reverse());

function sortByCreated(rows) {
  return (rows ?? []).sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0));
}

export async function wipeDatabase() {
  await tx('jobs', 'readwrite', (s) => s.clear());
  await tx('clips', 'readwrite', (s) => s.clear());
}

/** Rough usage figure for the settings screen; not all browsers implement it. */
export async function estimateUsage() {
  if (!navigator.storage?.estimate) return null;
  try { return await navigator.storage.estimate(); } catch { return null; }
}
