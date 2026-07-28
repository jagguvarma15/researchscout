// Batched fire-and-forget interaction beacons feeding /v1/events through the proxy. Events
// queue in memory and flush on a short timer, when the tab hides, and on pagehide via
// sendBeacon, so navigation never loses a batch and the page never waits on telemetry.

export type EventName = 'impression' | 'click' | 'dwell' | 'dismiss' | 'open_pdf';

export interface PaperEvent {
  event: EventName;
  paper_id: string;
  rank?: number;
  value?: number;
  surface?: string;
}

const FLUSH_AFTER_MS = 3000;
const FLUSH_AT = 50;

const queue: PaperEvent[] = [];
let timer: number | null = null;
let bound = false;

export function flushEvents(): void {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  if (queue.length === 0) return;
  const body = JSON.stringify({ events: queue.splice(0, queue.length) });
  const blob = new Blob([body], { type: 'application/json' });
  if (!navigator.sendBeacon?.('/api/events', blob)) {
    void fetch('/api/events', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => undefined);
  }
}

export function logEvent(event: PaperEvent): void {
  if (!bound) {
    bound = true;
    addEventListener('pagehide', flushEvents);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') flushEvents();
    });
  }
  queue.push(event);
  if (queue.length >= FLUSH_AT) flushEvents();
  else if (timer === null) timer = window.setTimeout(flushEvents, FLUSH_AFTER_MS);
}
