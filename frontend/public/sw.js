/* global self, clients */

const DEFAULT_URL = '/'

self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting())
})

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim())
})

const readPushPayload = event => {
  if (!event.data) return {}
  try {
    return event.data.json()
  } catch {
    return { body: event.data.text() }
  }
}

self.addEventListener('push', event => {
  const payload = readPushPayload(event)
  const payloadData = payload.data && typeof payload.data === 'object'
    ? payload.data
    : {}
  const data = {
    ...payloadData,
    url: payloadData.url || payload.url || DEFAULT_URL
  }

  event.waitUntil(self.registration.showNotification(
    payload.title || '\u7075\u9014 AI \u65c5\u884c\u52a9\u624b',
    {
      body: payload.body || '\u4f60\u7684\u65c5\u884c\u8ba1\u5212\u72b6\u6001\u5df2\u66f4\u65b0\u3002',
      tag: payload.tag || 'lingtu-trip-update',
      data
    }
  ))
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  const requestedUrl = event.notification.data?.url || DEFAULT_URL
  const targetUrl = new URL(requestedUrl, self.location.origin)
  const safeUrl = targetUrl.origin === self.location.origin
    ? targetUrl.href
    : self.location.origin

  event.waitUntil((async () => {
    const windowClients = await clients.matchAll({
      type: 'window',
      includeUncontrolled: true
    })
    const sameOriginClient = windowClients.find(
      client => new URL(client.url).origin === self.location.origin
    )
    if (sameOriginClient) {
      if ('navigate' in sameOriginClient) {
        await sameOriginClient.navigate(safeUrl)
      }
      return sameOriginClient.focus()
    }
    return clients.openWindow(safeUrl)
  })())
})
