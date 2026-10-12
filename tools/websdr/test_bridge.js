// Offline real-browser contract test. Never contacts a public receiver.
const http = require('http');
const assert = require('assert/strict');
const { spawn } = require('child_process');
let visits = 0, tuned;
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/tuned?')) { tuned = new URL(req.url, 'http://fixture').searchParams; res.end('ok'); return; }
  visits++;
  res.setHeader('Content-Type', 'text/html');
  res.end(`<script>
  var freq=0, mode='', lo=0, hi=0;
  function setfreqb(f) { freq=f; }
  function setmf(m,l,h) { mode=m.toUpperCase();lo=l;hi=h;
    fetch('/tuned?f='+freq+'&m='+mode+'&l='+lo+'&h='+hi);
    let n=0;setInterval(()=>{for(let i=0;i<800;i++) hook_webaud(Math.round(12000*Math.sin(2*Math.PI*1000*n++/8000)),8000);},100);
  }
  </script>`);
});
server.listen(0, '127.0.0.1', async () => {
  const child = spawn(process.execPath, [__dirname+'/websdr_bridge.js', `http://127.0.0.1:${server.address().port}/`, '7074', 'usb', '0', '3000'], {env:{...process.env, WEBSDR_DURATION_SECONDS:'4'}});
  let chunks=[], logs='';
  child.stdout.on('data', b=>chunks.push(b));
  child.stderr.on('data', b=>logs+=b);
  const timer=setTimeout(()=>child.kill('SIGTERM'),15000);
  child.on('exit', code=>{
    clearTimeout(timer);server.close();
    try {
      assert.equal(code,0,logs);assert.ok(tuned,'provider tuning API was not called');
      assert.deepEqual(Object.fromEntries(tuned),{f:'7074',m:'USB',l:'0',h:'3'});
      const pcm=Buffer.concat(chunks);assert.ok(pcm.length>8000,'PCM missing');
      let energy=0;for(let i=0;i<pcm.length;i+=2)energy+=pcm.readInt16LE(i)**2;
      assert.ok(Math.sqrt(energy/(pcm.length/2))>1000,'PCM silent');
      assert.match(logs,/WebSDR browser closed/);assert.equal(visits,1,'unexpected reconnect');
      console.log(`PASS tuning/PCM/cleanup: ${pcm.length} bytes, RMS ${Math.sqrt(energy/(pcm.length/2)).toFixed(1)}`);
    }catch(e){console.error(e);process.exitCode=1;}
  });
});
