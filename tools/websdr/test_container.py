"""Offline container smoke test: no host browser and no external network.
Run: /usr/bin/python3 tools/websdr/test_container.py radio-stream:websdr-test
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

IMAGE = sys.argv[1] if len(sys.argv) > 1 else 'radio-stream:websdr-test'
NAME = f'websdr-offline-{os.getpid()}'
def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True, **kwargs)

with tempfile.TemporaryDirectory(prefix='websdr-fixture-', dir=os.environ.get('TMPDIR')) as temp:
    root = Path(temp)
    root.chmod(0o755)
    (root/'launch.py').write_text('''import http.server, threading, subprocess, os, json
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
  if self.path.startswith('/tuned?'):
   Path('/run/radio/tuned.json').write_text(json.dumps(parse_qs(urlsplit(self.path).query)));return
  self.wfile.write(b"""<script>
var freq=0,mode='',lo=0,hi=0;
function setfreqb(f){freq=f;}
function setmf(m,l,h){mode=m.toUpperCase();lo=l;hi=h;
fetch('/tuned?f='+freq+'&m='+mode+'&l='+lo+'&h='+hi);
let n=0;setInterval(()=>{for(let i=0;i<800;i++)hook_webaud(Math.round(12000*Math.sin(2*Math.PI*1000*n++/8000)),8000);},100);}
</script>""")
server=http.server.HTTPServer(('127.0.0.1',8901),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
Path('/run/radio/receivers.json').write_text(json.dumps([dict(name='offline',type='websdr',min_khz=7000,max_khz=7300,url='http://127.0.0.1:8901/')]))
subprocess.run(['x11vnc','-storepasswd','fixture-only','/run/radio/vnc.pass'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
raise SystemExit(subprocess.call(['python3','-u','/opt/radio/radio.py']))
''')
    try:
        docker('run','-d','--name',NAME,'--init','--network','none','--shm-size','256m',
               '-v',f'{root}:/fixture:ro', '-e','RX_FREQ_KHZ=7074', '-e','RX_MODE=usb',
               '-e','RX_BANDWIDTH_HZ=3000','-e','RECEIVERS_FILE=/run/radio/receivers.json',
               '-e','VNC_PASSWORD_FILE=/run/radio/vnc.pass','-e','STREAM_OUTPUT=/run/radio/test.flv',
               '-e','WIDTH=640','-e','HEIGHT=360','-e','FPS=5',
               '--entrypoint','python3',IMAGE,'/fixture/launch.py')
        for _ in range(60):
            if 'Desktop ready' in docker('logs',NAME).stdout: break
            time.sleep(.5)
        else: raise AssertionError('desktop not ready: '+docker('logs',NAME).stderr)
        output=docker('exec',NAME,'python3','/opt/radio/session.py','--minutes','1','--state','/run/radio/usage.json',timeout=90)
        assert 'Session ended' in output.stdout,output
        assert 'first WebSDR audio samples received' in output.stderr,output
        print(output.stdout.strip())
        tuned=json.loads(docker('exec',NAME,'cat','/run/radio/tuned.json').stdout)
        assert tuned=={'f':['7074'],'m':['USB'],'l':['0'],'h':['3']},tuned
        probe=json.loads(docker('exec',NAME,'ffprobe','-v','error','-show_streams','-show_format','-of','json','/run/radio/test.flv').stdout)
        assert {s['codec_type'] for s in probe['streams']}=={'audio','video'}
        assert float(probe['format']['duration'])>50
        energy=docker('exec',NAME,'ffmpeg','-hide_banner','-i','/run/radio/test.flv','-af','volumedetect','-f','null','-').stderr
        import re
        db=float(re.search(r'mean_volume: ([-\d.]+) dB',energy)[1]);assert db>-30,db
        processes=docker('exec',NAME,'ps','-eo','stat,comm').stdout
        assert not any(name in line and not line.strip().startswith('Z') for line in processes.splitlines() for name in ('node','chrome','ffmpeg')),processes
        assert 'xterm' in processes and 'pulseaudio' in processes
        state=json.loads(docker('exec',NAME,'cat','/run/radio/usage.json').stdout)
        assert len(state)==1 and state[0][1]==1,state
        print(f'PASS: container tuning={tuned}; video/audio duration={probe["format"]["duration"]}s; mean audio={db}dB; receiver/encoder cleaned; desktop alive; quota persisted')
    finally:
        docker('rm','-f',NAME)
