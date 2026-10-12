// Decoded WebSDR audio to mono 8 kHz s16le; never reconnects.
// Audio-hook adaptation from baitisj/websdr-bench, copyright 2026 Jeffrey
// Baitis, MIT (LICENSE). Provider API evidence: PROVIDER.md.
const { chromium } = require('playwright');
const fs = require('fs/promises');
const net = require('net');
const [url, f, mode, low, high] = process.argv.slice(2);
let frequency = Number(f), selectedMode = mode, lo = Number(low), hi = Number(high);
const minFrequency = process.env.TUNING_MIN_KHZ === undefined ? 0 : Number(process.env.TUNING_MIN_KHZ);
const maxFrequency = process.env.TUNING_MAX_KHZ === undefined ? 30000 : Number(process.env.TUNING_MAX_KHZ);
const statusPath = process.env.TUNING_STATUS_FILE || '/run/radio/tuning-status.json';
const statusTemp = `${statusPath}.${process.pid}.tmp`;
const modeInverted = process.env.TUNING_MODE_INVERTED === '1';
const minHz = (minFrequency * 1000).toFixed(6), maxHz = (maxFrequency * 1000).toFixed(6);
// Hamlib NET rigctl asks for this serialized state during connection setup.
const hamlibState = `1
1
0
${minHz} ${maxHz} 0xe -1 -1 0x77e00007 0xf
0 0 0 0 0 0 0
${minHz} ${maxHz} 0xe 5000 100000 0x77e00007 0xf
0 0 0 0 0 0 0
0x1ff 1
0x1ff 0
0 0
0xc 2400
0xc 1800
0xc 3000
0xc 0
0x2 500
0x2 2400
0x2 50
0x2 0
0x10 300
0x10 2400
0x10 50
0x10 0
0x1 8000
0x1 2400
0x1 10000
0x20 15000
0x20 8000
0x40 230000
0 0
9990
9990
10000
0
10
10 20 30
0xffffffffffffffff
0xffffffffffffffff
0xfffffffff7ffffff
0xffffff7083ffffff
0xffffffffffffffff
0xffffffffffffffbf
vfo_ops=0x7ffffff
ptt_type=0x0
targetable_vfo=0x10c3
has_set_vfo=1
has_get_vfo=1
has_set_freq=1
has_get_freq=1
has_set_conf=1
has_get_conf=1
has_power2mW=1
has_mW2power=1
timeout=0
rig_model=1
rigctld_version=Hamlib NET WebSDR bridge
agc_levels=0=OFF 1=SUPERFAST 2=FAST 5=MEDIUM 3=SLOW 6=AUTO 4=USER
ctcss_list= 67.0 69.3 71.9 74.4 77.0 79.7 82.5 85.4 88.5 91.5 94.8 97.4 100.0 103.5 107.2 110.9 114.8 118.8 123.0 127.3 131.8 136.5 141.3 146.2 151.4 156.7 159.8 162.2 165.5 167.9 171.3 173.8 177.3 179.9 183.5 186.2 189.9 192.8 196.6 199.5 203.5 206.5 210.7 218.1 225.7 229.1 233.6 241.8 250.3 254.1
dcs_list= 17 23 25 26 31 32 36 43 47 50 51 53 54 65 71 72 73 74 114 115 116 122 125 131 132 134 143 145 152 155 156 162 165 172 174 205 212 223 225 226 243 244 245 246 251 252 255 261 263 265 266 271 274 306 311 315 325 331 332 343 346 351 356 364 365 371 411 412 413 423 431 432 445 446 452 454 455 462 464 465 466 503 506 516 523 526 532 546 565 606 612 624 627 631 632 654 662 664 703 712 723 731 732 734 743 754
done
`;
if (!/^https?:\/\//.test(url || '') || !Number.isFinite(frequency) || frequency <= 0 ||
    !['usb','lsb','am'].includes(selectedMode) || !Number.isFinite(lo) || !Number.isFinite(hi) ||
    !Number.isFinite(minFrequency) || !Number.isFinite(maxFrequency) ||
    minFrequency < 0 || minFrequency >= maxFrequency || frequency < minFrequency || frequency > maxFrequency ||
    lo >= hi || hi-lo > 12000) {
  console.error('Usage: websdr_bridge.js URL frequency_kHz mode low_Hz high_Hz');
  process.exit(2);
}
let browser, page, rigServer, closing = false, samples = 0, lastAudio = Date.now(), tuned = false;
const rigClients = new Set();
let rigQueue = Promise.resolve();
async function shutdown(code = 0) {
  if (closing) return;
  closing = true;
  const force = setTimeout(()=>process.exit(1),4000);
  if (rigServer) {
    for (const socket of rigClients) socket.destroy();
    rigServer.close();
    rigServer = null;
  }
  try { if (browser) await browser.close(); }
  catch { code = 1; }
  clearTimeout(force);
  console.error('WebSDR browser closed');
  process.exit(code || (samples === 0 ? 1 : 0));
}
process.once('SIGINT', ()=>shutdown());
process.once('SIGTERM', ()=>shutdown());
process.stdout.on('error', ()=>shutdown(1));

function rigError(code) {
  const error = new Error('Hamlib command failed');
  error.rigCode = code;
  throw error;
}

function pageModeFor(requestedMode) {
  if (!modeInverted) return requestedMode;
  if (requestedMode === 'usb') return 'lsb';
  if (requestedMode === 'lsb') return 'usb';
  return requestedMode;
}

async function writeTuningStatus(actual, requestedMode) {
  const status = {
    state: 'page_state', frequency_khz: actual.frequency,
    mode: requestedMode, page_mode: actual.mode.toLowerCase(),
    mode_inverted: modeInverted,
    passband_hz: [actual.lo * 1000, actual.hi * 1000]
  };
  if (process.env.TUNING_RECEIVER) status.receiver = process.env.TUNING_RECEIVER;
  await fs.writeFile(statusTemp, JSON.stringify(status));
  await fs.rename(statusTemp, statusPath);
}

async function applyTuning(nextFrequency, nextMode, bandwidthHz) {
  if (!Number.isFinite(nextFrequency) || nextFrequency < minFrequency || nextFrequency > maxFrequency ||
      !['usb', 'lsb', 'am'].includes(nextMode) || !Number.isInteger(bandwidthHz) ||
      bandwidthHz < 1 || bandwidthHz > 12000)
    rigError(-1);
  const pageMode = pageModeFor(nextMode);
  const [nextLow, nextHigh] = pageMode === 'usb' ? [0, bandwidthHz] :
    pageMode === 'lsb' ? [-bandwidthHz, 0] : [-bandwidthHz / 2, bandwidthHz / 2];
  const actual = await page.evaluate(({frequency, mode, low, high}) => {
    setfreqb(frequency);
    setmf(mode, low / 1000, high / 1000);
    return {frequency: window.freq, mode: window.mode, lo: window.lo, hi: window.hi};
  }, {frequency: nextFrequency, mode: pageMode, low: nextLow, high: nextHigh});
  if (actual.frequency !== nextFrequency || actual.mode.toLowerCase() !== pageMode ||
      Math.abs(actual.lo - nextLow / 1000) > 1e-6 || Math.abs(actual.hi - nextHigh / 1000) > 1e-6)
    rigError(-1);
  frequency = actual.frequency;
  selectedMode = nextMode;
  lo = actual.lo;
  hi = actual.hi;
  await writeTuningStatus(actual, nextMode);
}

function parseRigCommand(line) {
  let text = line.trim(), separator = null;
  if (['+', ';', '|', ','].includes(text[0])) {
    separator = text[0] === '+' ? '\n' : text[0];
    text = text.slice(1).trim();
  }
  const tokens = text.split(/\s+/).filter(Boolean);
  const raw = tokens.shift() || '';
  const long = raw.startsWith('\\');
  const command = long ? raw.slice(1).toLowerCase() : raw;
  const names = {f: 'get_freq', F: 'set_freq', m: 'get_mode', M: 'set_mode',
    v: 'get_vfo', V: 'set_vfo', t: 'get_ptt', T: 'set_ptt'};
  return {command, args: tokens, separator, name: long ? command : names[command] || command};
}

async function executeRigCommand(parsed) {
  const {command, args} = parsed;
  if (command === 'get_powerstat') return ['1'];
  if (command === 'chk_vfo') return ['0'];
  if (command === 'dump_state') return {raw: hamlibState};
  if (command === 'f' || command === 'get_freq') {
    const current = await page.evaluate(() => window.freq);
    return [String(Math.round(current * 1000))];
  }
  if (command === 'F' || command === 'set_freq') {
    const value = Number(args.length > 1 && /^VFO/i.test(args[0]) ? args[1] : args[0]);
    const current = await page.evaluate(() => ({low: window.lo, high: window.hi}));
    await applyTuning(value / 1000, selectedMode, Math.round(Math.abs(current.high - current.low) * 1000));
    return [];
  }
  if (command === 'm' || command === 'get_mode') {
    return [selectedMode.toUpperCase(), String(Math.round(Math.abs(hi - lo) * 1000))];
  }
  if (command === 'M' || command === 'set_mode') {
    const offset = args.length > 2 && /^VFO/i.test(args[0]) ? 1 : 0;
    const requestedMode = (args[offset] || '').toLowerCase();
    if (requestedMode === '?') return ['USB LSB AM'];
    if (!['usb', 'lsb', 'am'].includes(requestedMode)) rigError(-1);
    const requestedWidth = Number(args[offset + 1]);
    const width = requestedWidth === 0 ? Math.round(Math.abs(hi - lo) * 1000) : requestedWidth;
    await applyTuning(frequency, requestedMode, width);
    return [];
  }
  if (command === 'v' || command === 'get_vfo') return ['VFOA'];
  if (command === 'V' || command === 'set_vfo') {
    if (!['VFOA', 'VFO', 'CURRVFO'].includes((args[0] || '').toUpperCase())) rigError(-1);
    return [];
  }
  if (command === 't' || command === 'get_ptt') return ['0'];
  if (command === 'T' || command === 'set_ptt') rigError(-11);
  if (command === 'get_info') return ['WebSDR receive-only control'];
  rigError(-11);
}

function startRigControl() {
  const port = process.env.RIGCTLD_PORT === undefined ? 4532 : Number(process.env.RIGCTLD_PORT);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid RIGCTLD_PORT');
  rigServer = net.createServer(socket => {
    rigClients.add(socket);
    socket.on('close', () => rigClients.delete(socket));
    let pending = '';
    socket.on('data', chunk => {
      pending += chunk.toString('utf8');
      if (pending.length > 4096) return socket.destroy();
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        if (process.env.RIGCTLD_DEBUG === '1') console.error(`Hamlib command: ${line.trim()}`);
        const parsed = parseRigCommand(line);
        const task = rigQueue.then(async () => {
          try {
            const values = await executeRigCommand(parsed);
            if (values.raw !== undefined) {
              if (!socket.destroyed) socket.write(values.raw);
              return;
            }
            const result = parsed.separator ?
              `${parsed.name}${parsed.args.length ? ` ${parsed.args.join(' ')}` : ''}${parsed.separator}` +
                `${values.map(value => ` ${value}`).join(parsed.separator)}${values.length ? parsed.separator : ''}RPRT 0${parsed.separator}` :
              values.length ? `${values.join('\n')}\n` : 'RPRT 0\n';
            if (process.env.RIGCTLD_DEBUG === '1') console.error(`Hamlib reply: ${result.trim().replace(/\n/g, '|')}`);
            if (!socket.destroyed) socket.write(result);
          } catch (error) {
            const result = parsed.separator ? `RPRT ${error.rigCode || -1}${parsed.separator}` :
              `RPRT ${error.rigCode || -1}\n`;
            if (process.env.RIGCTLD_DEBUG === '1') console.error(`Hamlib reply: ${result.trim()}`);
            if (!socket.destroyed) socket.write(result);
          }
        });
        rigQueue = task.catch(error => console.error(`Hamlib command failed: ${error.message}`));
      }
    });
  });
  return new Promise((resolve, reject) => {
    rigServer.once('error', reject);
    rigServer.listen(port, '127.0.0.1', () => {
      rigServer.removeListener('error', reject);
      rigServer.on('error', error => {
        console.error(`Hamlib endpoint error: ${error.message}`);
        shutdown(1);
      });
      console.error(`Hamlib NET rigctl listening on 127.0.0.1:${rigServer.address().port}`);
      resolve();
    });
  });
}

setInterval(()=>{
  if (Date.now()-lastAudio > 15000) {
    console.error('WebSDR audio stalled; not reconnecting'); shutdown(1);
  }
},1000);
(async()=>{
  browser = await chromium.launch({args:['--autoplay-policy=no-user-gesture-required','--mute-audio']});
  page = await browser.newPage();
  page.on('crash', ()=>shutdown(1));
  page.on('websocket', socket=>socket.on('close', ()=>{ if (!closing) shutdown(1); }));
  let pos=0, last=0;
  await page.exposeFunction('pushAudio', (b64, rate)=>{
    if (!tuned || closing) return;
    if (!Number.isFinite(rate) || rate < 8000 || rate > 192000) return shutdown(1);
    const b=Buffer.from(b64,'base64');
    const x=new Int16Array(b.buffer,b.byteOffset,b.length/2), out=[];
    const step=rate/8000;
    while(pos<x.length) {
      const i=Math.floor(pos), frac=pos-i;
      const a=i<0?last:x[i], c=i+1<x.length?x[i+1]:x[i];
      out.push(Math.round(a+(c-a)*frac));pos+=step;
    }
    pos-=x.length;last=x[x.length-1];
    if (!out.length) return;
    if (!samples) console.error('first WebSDR audio samples received');
    samples+=out.length;lastAudio=Date.now();
    if (process.stdout.writableLength > 1024*1024) return shutdown(1);
    process.stdout.write(Buffer.from(new Int16Array(out).buffer));
  });
  await page.addInitScript(name=>{
    document.cookie=`username=${encodeURIComponent(name)}; path=/; SameSite=Lax`;
    window.webaud_hookdef=1;
    const q=[];let rate=0;
    Object.defineProperty(window,'hook_webaud',{get:()=>((sample,r)=>{
      if(q.length<192000)q.push(sample);rate=r;return sample;
    }),set:()=>{},configurable:false});
    setInterval(()=>{
      if(!q.length)return;
      const bytes=new Uint8Array(new Int16Array(q.splice(0)).buffer);
      let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));
      window.pushAudio(btoa(s),rate);
    },200);
  },(process.env.WEBSDR_NAME || 'N0CALL').slice(0,32));
  // PA3FWM WebSDR pages consume `tune=<kHz><mode>` during initialization.
  // Follow websdr-bench by supplying frequency and mode at page load.
  const initialUrl = new URL(url);
  initialUrl.searchParams.set('tune', `${frequency}${pageModeFor(selectedMode)}`);
  await page.goto(initialUrl.toString(),{waitUntil:'load',timeout:12000});
  await page.waitForFunction(()=>typeof setfreqb==='function' && typeof setmf==='function',{},{timeout:3000});
  // setfreqb selects band and may flip sideband; apply mode/filter AFTER it.
  // Both provider frequency and filter APIs use kHz, unlike our Hz passband.
  await applyTuning(frequency, selectedMode, Math.round(Math.abs(hi - lo)));
  tuned=true;
  console.error(`WebSDR page setting ${frequency} kHz ${selectedMode} -> ${modeInverted ? 'inverted ' : ''}${pageModeFor(selectedMode)} [${lo},${hi}] Hz`);
  await startRigControl();
  const start=page.locator('input[onclick^="chrome_audio_start"], input[onclick^="mozilla_audio_start"]');
  if(await start.count()) await start.first().click({timeout:2000});
})().catch(()=>{console.error('WebSDR startup failed; not reconnecting');shutdown(1);});
