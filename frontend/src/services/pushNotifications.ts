import {
  deletePushSubscription,
  fetchVapidPublicKey,
  savePushSubscription,
  type PushSubscriptionPayload
} from '@/services/api'

export type PushPermissionState = NotificationPermission | 'unsupported'

export interface PushUnsubscribeResult {
  hadSubscription: boolean
  unsubscribed: boolean
  serverRemoved: boolean
  cleanupError?: string
}

let registrationPromise: Promise<ServiceWorkerRegistration> | null = null

export const isPushSupported = (): boolean => (
  typeof window !== 'undefined'
  && window.isSecureContext
  && 'serviceWorker' in navigator
  && 'PushManager' in window
  && 'Notification' in window
)

export const getPushPermissionState = (): PushPermissionState => {
  if (!isPushSupported()) return 'unsupported'
  return Notification.permission
}

export const registerPushServiceWorker = async (): Promise<ServiceWorkerRegistration> => {
  if (!isPushSupported()) {
    throw new Error(
      typeof window !== 'undefined' && !window.isSecureContext
        ? '\u540e\u53f0\u901a\u77e5\u9700\u8981 HTTPS\uff08localhost \u5f00\u53d1\u73af\u5883\u9664\u5916\uff09'
        : '\u5f53\u524d\u6d4f\u89c8\u5668\u4e0d\u652f\u6301 Web Push'
    )
  }

  if (!registrationPromise) {
    registrationPromise = navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .catch(error => {
        registrationPromise = null
        throw error
      })
  }
  return registrationPromise
}

const getReadyRegistration = async (): Promise<ServiceWorkerRegistration> => {
  const registration = await registerPushServiceWorker()
  return registration.active ? registration : navigator.serviceWorker.ready
}

const decodeVapidKey = (publicKey: string): Uint8Array => {
  const padding = '='.repeat((4 - publicKey.length % 4) % 4)
  const base64 = (publicKey + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = window.atob(base64)
  const bytes = new Uint8Array(raw.length)
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index)
  }
  return bytes
}

const serializeSubscription = (
  subscription: PushSubscription
): PushSubscriptionPayload => {
  const json = subscription.toJSON()
  const endpoint = json.endpoint || subscription.endpoint
  const p256dh = json.keys?.p256dh
  const auth = json.keys?.auth
  if (!endpoint || !p256dh || !auth) {
    throw new Error('\u6d4f\u89c8\u5668\u8fd4\u56de\u7684\u63a8\u9001\u8ba2\u9605\u4fe1\u606f\u4e0d\u5b8c\u6574')
  }
  return {
    endpoint,
    expirationTime: json.expirationTime ?? subscription.expirationTime ?? null,
    keys: { p256dh, auth }
  }
}

export const getExistingPushSubscription = async (): Promise<PushSubscription | null> => {
  if (!isPushSupported()) return null
  const registration = await navigator.serviceWorker.getRegistration('/')
  return registration ? registration.pushManager.getSubscription() : null
}

export const syncExistingPushSubscription = async (): Promise<boolean> => {
  if (getPushPermissionState() !== 'granted') return false
  const subscription = await getExistingPushSubscription()
  if (!subscription) return false
  await savePushSubscription(serializeSubscription(subscription))
  return true
}

export const subscribeToPush = async (): Promise<PushSubscription> => {
  const permissionState = getPushPermissionState()
  if (permissionState === 'unsupported') {
    throw new Error('\u5f53\u524d\u73af\u5883\u4e0d\u652f\u6301 Web Push')
  }
  if (permissionState === 'denied') {
    throw new Error('\u901a\u77e5\u6743\u9650\u5df2\u88ab\u62d2\u7edd\uff0c\u8bf7\u5728\u6d4f\u89c8\u5668\u7f51\u7ad9\u8bbe\u7f6e\u4e2d\u91cd\u65b0\u5141\u8bb8')
  }

  const permission = permissionState === 'default'
    ? await Notification.requestPermission()
    : permissionState
  if (permission !== 'granted') {
    throw new Error('\u672a\u83b7\u5f97\u684c\u9762\u901a\u77e5\u6743\u9650')
  }

  const registration = await getReadyRegistration()
  let subscription = await registration.pushManager.getSubscription()
  if (!subscription) {
    const publicKey = await fetchVapidPublicKey()
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodeVapidKey(publicKey) as BufferSource
    })
  }
  await savePushSubscription(serializeSubscription(subscription))
  return subscription
}

export const unsubscribeFromPush = async (
  notifyServer: boolean = true
): Promise<PushUnsubscribeResult> => {
  const subscription = await getExistingPushSubscription()
  if (!subscription) {
    return { hadSubscription: false, unsubscribed: true, serverRemoved: true }
  }

  let serverRemoved = !notifyServer
  let cleanupError: string | undefined
  if (notifyServer) {
    try {
      const response = await deletePushSubscription(serializeSubscription(subscription))
      serverRemoved = response.success
    } catch (error: any) {
      cleanupError = error.message || '\u670d\u52a1\u7aef\u8ba2\u9605\u6e05\u7406\u5931\u8d25'
    }
  }

  const unsubscribed = await subscription.unsubscribe()
  if (!unsubscribed) {
    throw new Error('\u6d4f\u89c8\u5668\u672a\u80fd\u53d6\u6d88\u540e\u53f0\u901a\u77e5\u8ba2\u9605')
  }
  return { hadSubscription: true, unsubscribed, serverRemoved, cleanupError }
}
