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
session_process = None
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
Path('/run/radio/receivers.json').write_text(json.dumps([dict(name='offline',type='websdr',min_khz=7000,max_khz=7300,url='http://127.0.0.1:8901/',mode_inverted=True)]))
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
        session_process=subprocess.Popen(
            ['docker','exec',NAME,'python3','/opt/radio/session.py','--minutes','5'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        last_error=None
        for _ in range(30):
            if session_process.poll() is not None:
                stdout,stderr=session_process.communicate()
                raise AssertionError(f'session exited before Hamlib was ready: {stdout}\n{stderr}')
            try:
                frequency=docker('exec',NAME,'rigctl','-m','2','-r','127.0.0.1:4532','f').stdout.strip()
                break
            except subprocess.CalledProcessError as exc:
                last_error=f'{exc.stdout}\n{exc.stderr}'
                time.sleep(.5)
        else:
            logs=docker('logs',NAME)
            raise AssertionError(f'Hamlib NET rigctl endpoint did not become ready: {last_error}\n{logs.stdout}\n{logs.stderr}')
        assert frequency=='7074000',frequency
        docker('exec',NAME,'rigctl','-m','2','-r','127.0.0.1:4532','F','7100000')
        assert docker('exec',NAME,'rigctl','-m','2','-r','127.0.0.1:4532','f').stdout.strip()=='7100000'
        docker('exec',NAME,'rigctl','-m','2','-r','127.0.0.1:4532','M','LSB','2400')
        assert docker('exec',NAME,'rigctl','-m','2','-r','127.0.0.1:4532','m').stdout.splitlines()==['LSB','2400']
        time.sleep(4)
        docker('exec',NAME,'python3','-c',
               'import glob,os,signal; p=[int(f.split("/")[-2]) for f in glob.glob("/proc/[0-9]*/cmdline") '
               'if open(f,"rb").read().split(bytes([0]))[1:2]==[b"/opt/radio/session.py"]]; '
               'assert len(p)==1,p; os.kill(p[0],signal.SIGTERM)')
        output,errors=session_process.communicate(timeout=90)
        assert session_process.returncode==1,(output,errors)
        assert 'Session ended' in output,output
        assert 'Receiver ended' not in output,output
        assert 'first WebSDR audio samples received' in errors,errors
        print(output.strip())
        tuned=json.loads(docker('exec',NAME,'cat','/run/radio/tuned.json').stdout)
        assert tuned=={'f':['7100'],'m':['USB'],'l':['0'],'h':['2.4']},tuned
        status=json.loads(docker('exec',NAME,'cat','/run/radio/tuning-status.json').stdout)
        assert status=={'state':'page_state','frequency_khz':7100,'mode':'lsb',
                        'page_mode':'usb','mode_inverted':True,
                        'passband_hz':[0,2400],'receiver':'offline'},status
        windows=docker('exec',NAME,'xwininfo','-display',':99','-root','-tree').stdout
        assert 'Receiver tuning' in windows,windows
        probe=json.loads(docker('exec',NAME,'ffprobe','-v','error','-show_streams','-show_format','-of','json','/run/radio/test.flv').stdout)
        assert {s['codec_type'] for s in probe['streams']}=={'audio','video'}
        assert float(probe['format']['duration'])>2
        energy=docker('exec',NAME,'ffmpeg','-hide_banner','-i','/run/radio/test.flv','-af','volumedetect','-f','null','-').stderr
        import re
        db=float(re.search(r'mean_volume: ([-\d.]+) dB',energy)[1]);assert db>-30,db
        processes=docker('exec',NAME,'ps','-eo','stat,comm').stdout
        assert not any(name in line and not line.strip().startswith('Z') for line in processes.splitlines() for name in ('node','chrome','ffmpeg')),processes
        assert 'xterm' in processes and 'pulseaudio' in processes
        print(f'PASS: Hamlib tuned WebSDR; provider readback={status}; visible tuning HUD; video/audio duration={probe["format"]["duration"]}s; mean audio={db}dB; receiver/encoder cleaned; desktop alive')
    finally:
        docker('rm','-f',NAME)
        if session_process and session_process.poll() is None:
            session_process.communicate(timeout=10)
