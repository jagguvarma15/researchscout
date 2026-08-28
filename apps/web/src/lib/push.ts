// The client side of web push: register the worker, subscribe with the deployment's
// VAPID key, and mirror the subscription to the account. Everything here is opt-in from
// the settings panel - no page load registers a worker on its own.

function base64UrlToUint8Array(value: string): Uint8Array {
  const padded = value + '='.repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded.replaceAll('-', '+').replaceAll('_', '/'));
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}

export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

/** Whether this deployment offers push at all (the key route 404s when it does not). */
export async function pushOffered(): Promise<boolean> {
  if (!pushSupported()) return false;
  try {
    const response = await fetch('/api/me/push-key');
    return response.ok;
  } catch {
    return false;
  }
}

/** This browser's current subscription, if the worker is registered and subscribed. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration();
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

/** Turn notifications on: permission, worker, subscription, and the account mirror. */
export async function enablePush(): Promise<'on' | 'denied' | 'failed'> {
  if (!pushSupported()) return 'failed';
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') return 'denied';
  try {
    const keyResponse = await fetch('/api/me/push-key');
    if (!keyResponse.ok) return 'failed';
    const { key } = (await keyResponse.json()) as { key: string };
    const registration = await navigator.serviceWorker.register('/sw.js');
    const subscription =
      (await registration.pushManager.getSubscription()) ??
      (await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlToUint8Array(key).buffer as ArrayBuffer,
      }));
    const body = subscription.toJSON();
    const stored = await fetch('/api/me/push-subscriptions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ endpoint: body.endpoint, keys: body.keys }),
    });
    return stored.ok ? 'on' : 'failed';
  } catch {
    return 'failed';
  }
}

/** Turn notifications off: drop the account mirror, then the browser subscription. */
export async function disablePush(): Promise<void> {
  const subscription = await currentSubscription();
  if (!subscription) return;
  try {
    await fetch('/api/me/push-subscriptions', {
      method: 'DELETE',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
  } catch {
    // The browser-side unsubscribe below still stops the notices.
  }
  await subscription.unsubscribe();
}
