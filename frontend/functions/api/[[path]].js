import { proxyToApi } from '../_lib/proxy.js'

export function onRequest(context) {
  return proxyToApi(context)
}
