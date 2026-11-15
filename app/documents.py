import json
import os
import subprocess
import sys
from pathlib import Path
from .config import ROOT
from .security import fail

def decode(source,out):
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','PATH')}
    try:
        result=subprocess.run([sys.executable,'-I',str(ROOT/'app/render_document.py'),str(source),str(out)],env=env,capture_output=True,timeout=25,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode or len(result.stdout)>1024: raise ValueError('decode')
        return json.loads(result.stdout)
    except (ValueError,subprocess.TimeoutExpired,OSError):
        fail(422,'Unsupported, encrypted, damaged or over-limit document. Use PDF, PNG or JPEG with at most 10 pages.')
