// Decoded WebSDR audio to mono 8 kHz s16le; never reconnects.
// Audio-hook adaptation from baitisj/websdr-bench, copyright 2026 Jeffrey
// Baitis, MIT (LICENSE). Provider API evidence: PROVIDER.md.
const { chromium } = require('playwright');
const [url, f, mode, low, high] = process.argv.slice(2);
const frequency = Number(f), lo = Number(low), hi = Number(high);
const duration = Number(process.env.WEBSDR_DURATION_SECONDS || 1500);
if (!/^https?:\/\//.test(url || '') || !Number.isFinite(frequency) || frequency <= 0 ||
    !['usb','lsb','am'].includes(mode) || !Number.isFinite(lo) || !Number.isFinite(hi) ||
    lo >= hi || hi-lo > 12000 || !Number.isFinite(duration) || duration <= 0 || duration > 1500) {
  console.error('Usage: websdr_bridge.js URL frequency_kHz mode low_Hz high_Hz (duration <=1500s)');
  process.exit(2);
}
let browser, closing = false, samples = 0, lastAudio = Date.now(), tuned = false;
async function shutdown(code = 0) {
  if (closing) return;
  closing = true;
  const force = setTimeout(()=>process.exit(1),4000);
  try { if (browser) await browser.close(); }
  catch { code = 1; }
  clearTimeout(force);
  console.error('WebSDR browser closed');
  process.exit(code || (samples === 0 ? 1 : 0));
}
process.once('SIGINT', ()=>shutdown());
process.once('SIGTERM', ()=>shutdown());
process.stdout.on('error', ()=>shutdown(1));
setTimeout(()=>shutdown(), duration*1000);
setInterval(()=>{
  if (Date.now()-lastAudio > 15000) {
    console.error('WebSDR audio stalled; not reconnecting'); shutdown(1);
  }
},1000);
(async()=>{
  browser = await chromium.launch({args:['--autoplay-policy=no-user-gesture-required','--mute-audio']});
  const page = await browser.newPage();
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
  await page.goto(url,{waitUntil:'load',timeout:12000});
  await page.waitForFunction(()=>typeof setfreqb==='function' && typeof setmf==='function',{},{timeout:3000});
  // setfreqb selects band and may flip sideband; apply mode/filter AFTER it.
  // Both provider frequency and filter APIs use kHz, unlike our Hz passband.
  const actual=await page.evaluate(({frequency,mode,lo,hi})=>{
    setfreqb(frequency);setmf(mode,lo/1000,hi/1000);
    return {frequency:window.freq,mode:window.mode,lo:window.lo,hi:window.hi};
  },{frequency,mode,lo,hi});
  if(actual.frequency!==frequency || actual.mode.toLowerCase()!==mode ||
      Math.abs(actual.lo-lo/1000)>1e-6 || Math.abs(actual.hi-hi/1000)>1e-6)
    throw new Error('Provider did not accept tuning');
  tuned=true;
  console.error(`WebSDR tuned ${frequency} kHz ${mode} [${lo},${hi}] Hz`);
  const start=page.locator('input[onclick^="chrome_audio_start"], input[onclick^="mozilla_audio_start"]');
  if(await start.count()) await start.first().click({timeout:2000});
})().catch(()=>{console.error('WebSDR startup failed; not reconnecting');shutdown(1);});
