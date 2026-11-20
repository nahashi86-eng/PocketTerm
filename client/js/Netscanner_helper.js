'use strict';

/*
 * Netscanner_helper.js
 * Node.js worker for Network Globe. Uses only Node's built-in modules.
 * Input: one JSON object on stdin: { action, payload }
 * Output: one JSON object on stdout.
 * Scanning is limited to private IPv4 subnets passed by the Python UI.
 */
const os = require('os');
const net = require('net');
const dns = require('dns').promises;
const dgram = require('dgram');
const { execFile } = require('child_process');
const { promisify } = require('util');
const execFileAsync = promisify(execFile);

const PORTS = {
  22: 'SSH', 53: 'DNS', 80: 'HTTP', 135: 'Windows RPC', 139: 'NetBIOS',
  443: 'HTTPS', 445: 'SMB file sharing', 548: 'Apple file sharing',
  554: 'RTSP video', 631: 'IPP printing', 3389: 'Remote Desktop',
  5000: 'UPnP / NAS web', 5555: 'Android debug', 7000: 'AirPlay',
  8008: 'Google Cast', 8009: 'Google Cast', 8080: 'HTTP (alt)',
  8443: 'HTTPS (alt)', 9100: 'Raw printing', 62078: 'iOS device sync'
};
const ALIVE_PORTS = [80, 443, 445, 22, 135, 139, 8080, 62078, 5555, 8009];
const MAX_HOSTS_PER_NETWORK = 2046; // refuse unexpectedly large ranges

function isPrivateIPv4(ip) {
  if (net.isIP(ip) !== 4) return false;
  const p = ip.split('.').map(Number);
  return p[0] === 10 || (p[0] === 172 && p[1] >= 16 && p[1] <= 31) ||
    (p[0] === 192 && p[1] === 168);
}
function ipToInt(ip) { return ip.split('.').reduce((n, x) => ((n << 8) | Number(x)) >>> 0, 0); }
function intToIp(n) { return [n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join('.'); }
function networkHosts(cidr) {
  const [ip, bitsText] = cidr.split('/');
  const bits = Number(bitsText);
  if (net.isIP(ip) !== 4 || !Number.isInteger(bits) || bits < 21 || bits > 30 || !isPrivateIPv4(ip)) return [];
  const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
  const base = (ipToInt(ip) & mask) >>> 0;
  const count = 2 ** (32 - bits);
  if (count - 2 > MAX_HOSTS_PER_NETWORK) return [];
  const out = [];
  for (let i = 1; i < count - 1; i++) out.push(intToIp((base + i) >>> 0));
  return out;
}
function run(command, args, timeout = 5000) {
  return execFileAsync(command, args, { timeout, windowsHide: true, maxBuffer: 2 * 1024 * 1024 })
    .then(x => x.stdout || '').catch(() => '');
}
function pingHost(ip) {
  const windows = process.platform === 'win32';
  const args = windows ? ['-n', '1', '-w', '850', ip] : ['-c', '1', '-W', '1', ip];
  return run('ping', args, 4000).then(output => {
    const ttl = output.match(/ttl[=:\s]+(\d+)/i);
    if (!ttl) return null;
    const time = output.match(/time[=<]\s*([\d.]+)\s*ms/i);
    return { ttl: Number(ttl[1]), rtt: time ? Number(time[1]) : null };
  });
}
function tcpCheck(ip, port, timeout = 350) {
  return new Promise(resolve => {
    const socket = new net.Socket();
    let settled = false;
    const finish = value => { if (!settled) { settled = true; socket.destroy(); resolve(value); } };
    socket.setTimeout(timeout);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', err => finish(err && err.code === 'ECONNREFUSED'));
    socket.connect(port, ip);
  });
}
async function mapLimit(items, limit, fn) {
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      const index = next++;
      if (index >= items.length) return;
      await fn(items[index], index);
    }
  });
  await Promise.all(workers);
}
async function reverseName(ip) {
  try { const names = await dns.reverse(ip); return names[0] || null; }
  catch (_) { return null; }
}
async function readArp() {
  const output = await run(process.platform === 'win32' ? 'arp' : 'ip',
    process.platform === 'win32' ? ['-a'] : ['neigh'], 7000);
  const result = {};
  let m;
  const re = process.platform === 'win32'
    ? /(\d+\.\d+\.\d+\.\d+)\s+([\da-f]{2}(?:[:-][\da-f]{2}){5})/ig
    : /(\d+\.\d+\.\d+\.\d+).*?lladdr\s+([\da-f]{2}(?::[\da-f]{2}){5})/ig;
  while ((m = re.exec(output))) {
    const ip = m[1], mac = m[2].replace(/-/g, ':').toUpperCase();
    if (!mac.startsWith('FF:FF:FF') && mac !== '00:00:00:00:00:00') result[ip] = mac;
  }
  return result;
}

const NBSTAT_QUERY = Buffer.concat([
  Buffer.from('a2480000000100000000000020', 'hex'),
  Buffer.from('434b', 'hex'), Buffer.alloc(30, 0x41),
  Buffer.from('0000210001', 'hex')
]);
const MDNS_QUERY = Buffer.from('000000000001000000000000095f7365727669636573075f646e732d736404756470056c6f63616c00000c0001', 'hex');
const SSDP_QUERY = Buffer.from('M-SEARCH * HTTP/1.1\\r\\nHOST: 239.255.255.250:1900\\r\\nMAN: "ssdp:discover"\\r\\nMX: 2\\r\\nST: ssdp:all\\r\\n\\r\\n');

function parseNbstat(buf) {
  try {
    let offset = 57;
    const count = buf[56];
    for (let i = 0; i < count; i++, offset += 18) {
      const name = buf.subarray(offset, offset + 15).toString('latin1').trim();
      const suffix = buf[offset + 15], flags = buf[offset + 16];
      if (suffix === 0 && !(flags & 0x80) && name) return name;
    }
  } catch (_) {}
  return null;
}

function udpDiscovery(localIp, ips) {
  return new Promise(resolve => {
    const found = new Map();
    const socket = dgram.createSocket({ type: 'udp4', reuseAddr: true });
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      try { socket.close(); } catch (_) {}
      resolve(found);
    };
    socket.on('error', done);
    socket.on('message', (msg, rinfo) => {
      if (!ips.includes(rinfo.address)) return;
      const name = parseNbstat(msg);
      const old = found.get(rinfo.address) || {};
      if (name) old.name = name;
      old.discovered = true;
      found.set(rinfo.address, old);
    });
    socket.bind(0, localIp, () => {
      try {
        socket.setMulticastInterface(localIp);
        socket.setMulticastTTL(2);
      } catch (_) {}
      for (const ip of ips) {
        try { socket.send(NBSTAT_QUERY, 137, ip); } catch (_) {}
      }
      try { socket.send(MDNS_QUERY, 5353, '224.0.0.251'); } catch (_) {}
      try { socket.send(SSDP_QUERY, 1900, '239.255.255.250'); } catch (_) {}
      setTimeout(done, 3000);
    });
  });
}

async function scan(payload) {
  const hosts = new Map();
  for (const iface of payload || []) {
    const local = iface.ip;
    const ips = networkHosts(iface.cidr);
    if (!ips.length || !isPrivateIPv4(local)) continue;
    function add(ip, details = {}) {
      if (!isPrivateIPv4(ip) || !ips.includes(ip) && ip !== local) return;
      const previous = hosts.get(ip) || { ip };
      Object.assign(previous, Object.fromEntries(Object.entries(details).filter(([,v]) => v !== null && v !== undefined)));
      hosts.set(ip, previous);
    }
    add(local, { name: os.hostname() });
    await mapLimit(ips, 64, async ip => {
      const ping = await pingHost(ip);
      if (ping) add(ip, ping);
    });
    const unresolved = ips.filter(ip => !hosts.has(ip));
    await mapLimit(unresolved, 80, async ip => {
      for (const port of ALIVE_PORTS) {
        if (await tcpCheck(ip, port)) { add(ip); break; }
      }
    });
    // UDP discovery finds devices that ignore ping and have no probed TCP ports open.
    const udpFound = await udpDiscovery(local, ips);
    for (const [ip, details] of udpFound.entries()) add(ip, details);
    const arp = await readArp();
    for (const [ip, mac] of Object.entries(arp)) {
      if (ip === local || ips.includes(ip)) add(ip, { mac });
    }
  }
  const list = [...hosts.values()];
  await mapLimit(list, 24, async item => { if (!item.name) item.name = await reverseName(item.ip); });
  return { hosts: list };
}
async function probe(payload) {
  const ip = payload && payload.ip;
  if (!isPrivateIPv4(ip)) throw new Error('Probe target must be a private IPv4 address on the local network.');
  const ports = {};
  const entries = Object.entries(PORTS);
  await mapLimit(entries, 16, async ([port, label]) => {
    if (await tcpCheck(ip, Number(port), 650)) ports[port] = label;
  });
  let hostname = payload.isSelf ? os.hostname() : await reverseName(ip);
  let web = null;
  const webPort = [80, 8080].find(p => ports[p]);
  if (webPort) web = await httpPeek(ip, webPort);
  return { hostname, ports, web };
}
function httpPeek(ip, port) {
  return new Promise(resolve => {
    const socket = net.createConnection({ host: ip, port, timeout: 1100 });
    let data = '';
    let finished = false;
    const done = value => { if (!finished) { finished = true; socket.destroy(); resolve(value); } };
    socket.once('connect', () => socket.write(`GET / HTTP/1.0\r\nHost: ${ip}\r\nUser-Agent: NetworkGlobe\r\n\r\n`));
    socket.on('data', chunk => { data += chunk.toString('latin1'); if (data.length >= 6000) done(parseHttp(data)); });
    socket.once('end', () => done(parseHttp(data)));
    socket.once('timeout', () => done(parseHttp(data)));
    socket.once('error', () => done(null));
  });
}
function parseHttp(text) {
  if (!text) return null;
  const server = text.match(/^Server:\s*(.+?)\s*$/im);
  const title = text.match(/<title[^>]*>(.*?)<\/title>/is);
  return { server: server ? server[1].slice(0, 80) : null,
    title: title ? title[1].replace(/\s+/g, ' ').trim().slice(0, 80) : null };
}
async function resolveNames(ips) {
  const names = {};
  await mapLimit(ips || [], 24, async ip => { if (isPrivateIPv4(ip)) names[ip] = await reverseName(ip); });
  return { names };
}

async function main() {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  try {
    const request = JSON.parse(input || '{}');
    let result;
    if (request.action === 'scan') result = await scan(request.payload);
    else if (request.action === 'probe') result = await probe(request.payload);
    else if (request.action === 'resolve') result = await resolveNames(request.payload);
    else throw new Error('Unknown action: ' + request.action);
    process.stdout.write(JSON.stringify({ ok: true, result }));
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(error && error.message || error) }));
    process.exitCode = 1;
  }
}
main();
