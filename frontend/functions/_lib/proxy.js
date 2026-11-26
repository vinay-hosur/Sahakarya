export async function proxyToApi({ request, env }) {
  if (!env.API_BASE_URL) {
    return new Response('API_BASE_URL is not configured.', { status: 503 })
  }

  let apiOrigin
  try {
    apiOrigin = new URL(env.API_BASE_URL)
  } catch {
    return new Response('API_BASE_URL is invalid.', { status: 500 })
  }
  if (apiOrigin.protocol !== 'https:' || apiOrigin.username || apiOrigin.password) {
    return new Response('API_BASE_URL must be an HTTPS origin.', { status: 500 })
  }

  const incomingUrl = new URL(request.url)
  const target = new URL(apiOrigin.origin)
  target.pathname = incomingUrl.pathname
  target.search = incomingUrl.search

  const upstream = new Request(target, request)
  upstream.headers.set('x-forwarded-host', incomingUrl.host)
  upstream.headers.set('x-forwarded-proto', incomingUrl.protocol.slice(0, -1))
  return fetch(upstream)
}
