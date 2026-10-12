// Offline real-browser contract test. Never contacts a public receiver.
const http = require('http');
const assert = require('assert/strict');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
let visits = 0, tuned, initialTune;
const statusPath = path.join(os.tmpdir(), `websdr-tuning-${process.pid}.json`);
async function testHamlib(port) {
  const rigctl = args => new Promise((resolve, reject) => {
    const child = spawn('rigctl', ['-m', '2', '-r', `127.0.0.1:${port}`, ...args]);
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => stdout += chunk);
    child.stderr.on('data', chunk => stderr += chunk);
    child.on('error', reject);
    child.on('close', code => code === 0 ? resolve(stdout.trim()) :
      reject(new Error(`rigctl ${args.join(' ')} failed (${code}): ${stderr}`)));
  });
  const socket = net.createConnection(port, '127.0.0.1');
  let buffer = '', lines = [], waiters = [];
  socket.on('data', chunk => {
    buffer += chunk.toString();
    const ready = buffer.split('\n');
    buffer = ready.pop();
    for (const line of ready) {
      const waiter = waiters.shift();
      if (waiter) waiter(line);
      else lines.push(line);
    }
  });
  await new Promise((resolve, reject) => {
    socket.once('connect', resolve);
    socket.once('error', reject);
  });
  const command = async (text, count = 1) => {
    socket.write(`${text}\n`);
    const result = [];
    while (result.length < count) {
      result.push(lines.length ? lines.shift() : await new Promise(resolve => waiters.push(resolve)));
    }
    return result;
  };
  try {
    assert.deepEqual(await command('f'), ['7074000']);
    assert.deepEqual(await command('F 7100000'), ['RPRT 0']);
    assert.deepEqual(await command('M LSB 2400'), ['RPRT 0']);
    assert.deepEqual(await command('f'), ['7100000']);
    assert.deepEqual(await command('m', 2), ['LSB', '2400']);
    assert.deepEqual(await command('F 1000000'), ['RPRT -1']);
    assert.deepEqual(await command('f'), ['7100000']);
    assert.deepEqual(await command('T 1'), ['RPRT -11']);
    assert.equal(await rigctl(['f']), '7100000');
    await rigctl(['F', '7120000']);
    assert.equal(await rigctl(['f']), '7120000');
    await rigctl(['M', 'USB', '1800']);
    assert.equal(await rigctl(['m']), 'USB\n1800');
  } finally {
    socket.destroy();
  }
}
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/tuned?')) { tuned = new URL(req.url, 'http://fixture').searchParams; res.end('ok'); return; }
  visits++;
  initialTune = new URL(req.url, 'http://fixture').searchParams.get('tune');
  res.setHeader('Content-Type', 'text/html');
  res.end(`<script>
  var freq=0, mode='', lo=0, hi=0;
  function setfreqb(f) { freq=f; mode='LSB'; lo=-3; hi=0; }
  function setmf(m,l,h) { mode=m.toUpperCase();lo=l;hi=h;
    fetch('/tuned?f='+freq+'&m='+mode+'&l='+lo+'&h='+hi);
    let n=0;setInterval(()=>{for(let i=0;i<800;i++) hook_webaud(Math.round(12000*Math.sin(2*Math.PI*1000*n++/8000)),8000);},100);
  }
  </script>`);
});
server.listen(0, '127.0.0.1', async () => {
  const child = spawn(process.execPath, [__dirname+'/websdr_bridge.js', `http://127.0.0.1:${server.address().port}/`, '7074', 'usb', '0', '3000'], {env:{...process.env, TUNING_STATUS_FILE:statusPath, TUNING_MIN_KHZ:'7000', TUNING_MAX_KHZ:'7300', TUNING_MODE_INVERTED:'0', RIGCTLD_PORT:'0', RIGCTLD_DEBUG:'1'}});
  let chunks=[], logs='';
  let controlError;
  child.stdout.on('data', b=>chunks.push(b));
  child.stderr.on('data', b=>logs+=b);
  const timer=setTimeout(async()=>{
    try {
      const match=logs.match(/Hamlib NET rigctl listening on 127\.0\.0\.1:(\d+)/);
      assert.ok(match,'Hamlib control endpoint did not start');
      await testHamlib(Number(match[1]));
    }catch(e){controlError=new Error(`${e.message}\n${logs}`);}
    child.kill('SIGTERM');
  },2500);
  child.on('exit', code=>{
    clearTimeout(timer);server.close();
    try {
      assert.equal(code,0,logs);assert.ok(tuned,'provider tuning API was not called');
      assert.equal(initialTune,'7074usb','WebSDR did not receive initial frequency and mode');
      assert.ifError(controlError);
      assert.deepEqual(Object.fromEntries(tuned),{f:'7120',m:'USB',l:'0',h:'1.8'});
      assert.deepEqual(JSON.parse(fs.readFileSync(statusPath,'utf8')),
        {state:'page_state',frequency_khz:7120,mode:'usb',page_mode:'usb',
          mode_inverted:false,passband_hz:[0,1800]});
      const pcm=Buffer.concat(chunks);assert.ok(pcm.length>8000,'PCM missing');
      let energy=0;for(let i=0;i<pcm.length;i+=2)energy+=pcm.readInt16LE(i)**2;
      assert.ok(Math.sqrt(energy/(pcm.length/2))>1000,'PCM silent');
      assert.match(logs,/WebSDR browser closed/);assert.equal(visits,1,'unexpected reconnect');
      fs.unlinkSync(statusPath);
      console.log(`PASS Hamlib set/get tuning, PCM/cleanup: ${pcm.length} bytes, RMS ${Math.sqrt(energy/(pcm.length/2)).toFixed(1)}`);
    }catch(e){console.error(e);process.exitCode=1;}
  });
});
