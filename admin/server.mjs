import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const host = process.env.HOST || '0.0.0.0';
const port = Number.parseInt(process.env.PORT || '5173', 10);
const apiTarget = process.env.API_TARGET || 'http://127.0.0.1:8080';
const placeholderHosts = new Set(['server_ip', 'api_host']);

const mimeTypes = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
  ['.png', 'image/png'],
  ['.ico', 'image/x-icon'],
]);

function safePath(requestUrl) {
  const url = new URL(requestUrl, `http://${host}:${port}`);
  const pathname = url.pathname === '/' ? '/publishing-admin.html' : url.pathname;
  const resolved = normalize(join(__dirname, pathname));
  if (!resolved.startsWith(__dirname)) {
    return null;
  }
  return resolved;
}

function formatProxyError(error, target) {
  const targetHost = target.hostname.toLowerCase();
  if (placeholderHosts.has(targetHost)) {
    return [
      `API proxy error: ${error.message}`,
      '',
      `API_TARGET contains placeholder host "${target.hostname}".`,
      'Replace SERVER_IP/API_HOST with the real FastAPI server IP or DNS name, for example:',
      'API_TARGET=http://192.0.2.10:8080 PORT=5173 npm start',
      '',
      'If FastAPI runs on the same machine as this npm admin server, omit API_TARGET or use:',
      'API_TARGET=http://127.0.0.1:8080 PORT=5173 npm start',
    ].join('\n');
  }

  if (error.code === 'ENOTFOUND' || error.code === 'EAI_AGAIN') {
    return [
      `API proxy error: ${error.message}`,
      '',
      `Could not resolve API_TARGET host "${target.hostname}".`,
      'Check that API_TARGET is a reachable URL for the FastAPI server.',
    ].join('\n');
  }

  return `API proxy error: ${error.message}`;
}

function proxyApi(request, response) {
  const target = new URL(request.url || '/', apiTarget);
  const client = target.protocol === 'https:' ? httpsRequest : httpRequest;
  const proxyRequest = client(target, {
    method: request.method,
    headers: {
      ...request.headers,
      host: target.host,
    },
  }, proxyResponse => {
    response.writeHead(proxyResponse.statusCode || 502, {
      ...proxyResponse.headers,
      'x-admin-api-target': apiTarget,
      'x-admin-proxied-url': target.toString(),
    });
    proxyResponse.pipe(response);
  });

  proxyRequest.on('error', error => {
    response.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end(formatProxyError(error, target));
  });

  request.pipe(proxyRequest);
}

const server = createServer(async (request, response) => {
  const requestUrl = new URL(request.url || '/', `http://${host}:${port}`);
  if (requestUrl.pathname.startsWith('/api/')) {
    proxyApi(request, response);
    return;
  }

  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405, { Allow: 'GET, HEAD' });
    response.end('Method Not Allowed');
    return;
  }

  const path = safePath(request.url || '/');
  if (!path) {
    response.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Forbidden');
    return;
  }

  try {
    const info = await stat(path);
    if (!info.isFile()) {
      throw new Error('Not a file');
    }
    response.writeHead(200, {
      'Content-Type': mimeTypes.get(extname(path)) || 'application/octet-stream',
      'Content-Length': info.size,
      'Cache-Control': 'no-store',
    });
    if (request.method === 'HEAD') {
      response.end();
      return;
    }
    createReadStream(path).pipe(response);
  } catch {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Not Found');
  }
});

server.listen(port, host, () => {
  const displayHost = host === '0.0.0.0' ? 'SERVER_IP' : host;
  console.log(`Publishing admin is running: http://${displayHost}:${port}`);
  console.log(`Proxying /api/* requests to ${apiTarget}`);
  console.log('Listening on all network interfaces by default. Set HOST=127.0.0.1 to restrict access to this machine only.');
});
