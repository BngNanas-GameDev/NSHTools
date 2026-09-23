// Global toast bus — file terpisah agar Fast Refresh App.tsx tetap jalan
// (file yang export non-komponen tidak bisa di-Fast-Refresh).
export type ToastType = 'success' | 'warning' | 'danger' | 'violet' | 'accent' | 'muted'

export interface ToastItem {
  id: number
  message: string
  type: ToastType
}

let _toastId = 0
const _listeners: ((t: ToastItem) => void)[] = []

export function pushToast(message: string, type: ToastType = 'accent') {
  const t = { id: ++_toastId, message, type }
  _listeners.forEach((fn) => fn(t))
}

export function onToast(fn: (t: ToastItem) => void): () => void {
  _listeners.push(fn)
  return () => {
    const i = _listeners.indexOf(fn)
    if (i >= 0) _listeners.splice(i, 1)
  }
}
