/**
 * Haumex Labs Inc. AI Voice Caller - frontend server (Node built-ins only).
 *
 *   npm install   (once)
 *   npm start     (compiles TypeScript, then runs this server)
 *
 * Serves the call page and forwards /api requests and the /api/ws WebSocket to
 * the FastAPI backend, so the browser talks to a single origin and no secret
 * ever reaches the browser.
 *
 * Environment: PORT (5173), HOST (127.0.0.1), BACKEND_URL (http://127.0.0.1:8000)
 */
import http from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT ?? 5173);
const HOST = process.env.HOST ?? '127.0.0.1';
const BACKEND = new URL(process.env.BACKEND_URL ?? 'http://127.0.0.1:8000');
const BACKEND_PORT = Number(BACKEND.port || 80);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.ts': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.mp3': 'audio/mpeg',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json; charset=utf-8',
};

/** Only these folders are public. Everything else (node_modules, configs) is not served. */
const STATIC_ROUTES = [
  { prefix: '/assets/', dir: path.join(ROOT, 'public', 'assets'), exts: null },
  { prefix: '/dist/', dir: path.join(ROOT, 'dist'), exts: ['.js', '.map'] },
  { prefix: '/src/', dir: path.join(ROOT, 'src'), exts: ['.css', '.ts'] },
];

const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'same-origin',
  'Permissions-Policy': 'microphone=(self), camera=()',
};

function resolveStatic(urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath);
  } catch {
    return null;
  }
  if (decoded === '/' || decoded === '/index.html') return path.join(ROOT, 'index.html');
  for (const route of STATIC_ROUTES) {
    if (!decoded.startsWith(route.prefix)) continue;
    const target = path.normalize(path.join(route.dir, decoded.slice(route.prefix.length)));
    if (!target.startsWith(route.dir + path.sep)) return null; // path traversal
    if (route.exts && !route.exts.includes(path.extname(target).toLowerCase())) return null;
    return target;
  }
  return null;
}

function sendStatic(req, res, urlPath) {
  const file = resolveStatic(urlPath);
  let stat = null;
  try {
    stat = file ? statSync(file) : null;
  } catch {
    stat = null;
  }
  if (!file || !stat || !stat.isFile()) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8', ...SECURITY_HEADERS });
    res.end('Not found');
    return;
  }
  res.writeHead(200, {
    'Content-Type': MIME[path.extname(file).toLowerCase()] ?? 'application/octet-stream',
    'Content-Length': stat.size,
    'Cache-Control': 'no-cache',
    ...SECURITY_HEADERS,
  });
  if (req.method === 'HEAD') {
    res.end();
    return;
  }
  createReadStream(file).pipe(res);
}

function proxyHttp(req, res) {
  const upstream = http.request(
    {
      hostname: BACKEND.hostname,
      port: BACKEND_PORT,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: BACKEND.host },
    },
    (up) => {
      res.writeHead(up.statusCode ?? 502, up.headers);
      up.pipe(res);
    },
  );
  upstream.on('error', () => {
    if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ detail: `The backend is not reachable at ${BACKEND.origin}. Start it, then try again.` }));
  });
  req.pipe(upstream);
}

function proxyWebSocket(req, socket, head) {
  const upstream = http.request({
    hostname: BACKEND.hostname,
    port: BACKEND_PORT,
    path: req.url,
    method: 'GET',
    headers: { ...req.headers, host: BACKEND.host },
  });
  upstream.on('upgrade', (up, upSocket, upHead) => {
    let response = 'HTTP/1.1 101 Switching Protocols\r\n';
    for (let i = 0; i < up.rawHeaders.length; i += 2) response += `${up.rawHeaders[i]}: ${up.rawHeaders[i + 1]}\r\n`;
    socket.write(`${response}\r\n`);
    if (upHead?.length) socket.write(upHead);
    if (head?.length) upSocket.write(head);
    upSocket.pipe(socket);
    socket.pipe(upSocket);
    const close = () => {
      socket.destroy();
      upSocket.destroy();
    };
    socket.on('error', close);
    upSocket.on('error', close);
    socket.on('close', close);
    upSocket.on('close', close);
  });
  // Backend refused the upgrade (e.g. 403 for a bad Origin or unknown call).
  upstream.on('response', (up) => {
    socket.end(`HTTP/1.1 ${up.statusCode} ${up.statusMessage}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    up.resume();
  });
  upstream.on('error', () => {
    socket.end('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
  });
  socket.on('error', () => upstream.destroy());
  upstream.end();
}

const server = http.createServer((req, res) => {
  const url = req.url ?? '/';
  if (url === '/api' || url.startsWith('/api/')) {
    proxyHttp(req, res);
    return;
  }
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { Allow: 'GET, HEAD', ...SECURITY_HEADERS });
    res.end();
    return;
  }
  sendStatic(req, res, url.split('?')[0]);
});

server.on('upgrade', (req, socket, head) => {
  if (req.url?.startsWith('/api/ws/')) proxyWebSocket(req, socket, head);
  else socket.destroy();
});

server.on('error', (error) => {
  if (error.code === 'EADDRINUSE') {
    console.error(`Port ${PORT} is already in use. Close the other program or set PORT, for example: $env:PORT=5174; npm start`);
  } else {
    console.error(error);
  }
  process.exit(1);
});

if (!existsSync(path.join(ROOT, 'dist', 'main.js'))) {
  console.error('dist/main.js is missing. Run "npm start" (it compiles first) or "npm run build".');
  process.exit(1);
}

server.listen(PORT, HOST, () => {
  console.log('Haumex Labs Inc. AI Voice Caller');
  console.log(`  Open:     http://localhost:${PORT}`);
  console.log(`  Backend:  ${BACKEND.origin} (forwarding /api and /api/ws)`);
  console.log('  Stop:     Ctrl+C');
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
