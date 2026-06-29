const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'host',
])

function backendOrigin(env) {
  const origin = env.BACKEND_ORIGIN || env.AHCC_BACKEND_ORIGIN
  return origin ? origin.replace(/\/+$/, '') : ''
}

function proxyHeaders(request) {
  const headers = new Headers(request.headers)
  for (const header of HOP_BY_HOP_HEADERS) {
    headers.delete(header)
  }
  return headers
}

function preflight() {
  return new Response(null, {
    status: 204,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      'Access-Control-Max-Age': '86400',
    },
  })
}

export async function proxyToBackend(request, env) {
  if (request.method === 'OPTIONS') {
    return preflight()
  }

  const origin = backendOrigin(env)
  if (!origin) {
    return Response.json(
      {
        error: 'BACKEND_ORIGIN is not configured',
        detail: 'Set BACKEND_ORIGIN to the public FastAPI origin, for example https://api.example.com.',
      },
      { status: 502 },
    )
  }

  const incomingUrl = new URL(request.url)
  const targetUrl = `${origin}${incomingUrl.pathname}${incomingUrl.search}`
  const init = {
    method: request.method,
    headers: proxyHeaders(request),
    body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
    redirect: 'manual',
  }

  const response = await fetch(new Request(targetUrl, init))
  const responseHeaders = new Headers(response.headers)
  responseHeaders.set('Access-Control-Allow-Origin', '*')
  responseHeaders.set('Cache-Control', responseHeaders.get('Cache-Control') || 'no-store')

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  })
}
