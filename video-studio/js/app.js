// Bootstrap. Order matters: the composer owns the settings-derived controls, the panels
// subscribe to queue events, and the queue is restored last so resumed jobs have a UI to
// render into.

import { initComposer } from './composer.js';
import { initPanels } from './panels.js';
import { initSettings } from './settings.js';
import { initQueue } from './queue.js';
import { toast } from './util.js';

async function main() {
  initComposer();
  initPanels();
  initSettings();
  await initQueue();

  // A refresh mid-render is survivable, but a close is not always — warn while work is live.
  window.addEventListener('beforeunload', (e) => {
    const { listJobs } = window.__videoStudio ?? {};
    if (!listJobs) return;
    const live = listJobs().some((j) => j.status === 'running' || j.status === 'queued');
    if (live) { e.preventDefault(); e.returnValue = ''; }
  });
}

main().catch((err) => {
  console.error(err);
  toast(`Failed to start: ${err.message ?? err}`, 'bad');
});

// Exposed for the unload guard and for poking at state from the console.
import * as queue from './queue.js';
window.__videoStudio = queue;
