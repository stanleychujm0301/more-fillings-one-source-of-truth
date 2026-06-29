import { proxyToBackend } from './_proxy.js'

export async function onRequest(context) {
  return proxyToBackend(context.request, context.env)
}
