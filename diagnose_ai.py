"""Synthetic diagnostic only. Never prints the credential or invoice content."""
from contextlib import contextmanager
import httpx
import sys
from app import ai
from app.config import ROOT
cfg=ai.credentials()
original=httpx.Client.stream
@contextmanager
def observed(self,*args,**kwargs):
    with original(self,*args,**kwargs) as response:
        print('Provider HTTP status:',response.status_code)
        if response.status_code!=200:
            response.read()
            try: message=response.json().get('error',{}).get('message','')
            except Exception: message='Non JSON provider response.'
            print(message.replace(cfg['api_key'],'[REDACTED]')[:1500])
        else:
            response.read()
            candidate=response.json().get('candidates',[{}])[0]
            print('Finish reason:',candidate.get('finishReason'))
        yield response
httpx.Client.stream=observed
import tempfile
from pathlib import Path
from app.documents import decode
with tempfile.TemporaryDirectory() as t:
    filename=sys.argv[1] if len(sys.argv)>1 else 'DEMO-1042.png'
    if filename not in ('DEMO-1042.png','DEMO-1043.pdf'): raise SystemExit('Synthetic fixtures only.')
    folder=Path(t); metadata=decode(ROOT/'examples'/filename,folder/'pages')
    try:
        value,model=ai.extract(folder/'pages',metadata['page_count'],cfg)
        print('Valid structured invoice returned:',bool(value))
    except Exception as e: print('Validation outcome:',str(e).replace(cfg['api_key'],'[REDACTED]')[:300])
