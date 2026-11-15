"""Opt-in live evaluation; failures remain in the denominator and every run is retained."""
import argparse
import hashlib
import json
import tempfile
import time
import unicodedata
from datetime import datetime,timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from app import ai
from app.documents import decode
from app.config import ROOT

NUMERIC={'quantity','unit_price','tax','discount','declared_total'}
def normalized(value):return ' '.join(unicodedata.normalize('NFKC',str(value)).casefold().split())
def compare(actual,expected):
    checks={}
    def check(path,a,e):
        if e is None:checks[path]=a is None
        elif a is None:checks[path]=False
        elif path.split('.')[-1] in NUMERIC:
            try:checks[path]=Decimal(str(a))==Decimal(str(e))
            except (InvalidOperation,ValueError):checks[path]=False
        else:checks[path]=normalized(a)==normalized(e)
    for key,value in expected.items():
        if key=='lines':
            lines=actual.get('lines',[])
            checks['line_count']=len(lines)==len(value)
            for i,line in enumerate(value):
                for field,answer in line.items():check(f'lines.{i}.{field}',lines[i].get(field) if i<len(lines) else object(),answer)
        else:check(key,actual.get(key,object()),value)
    return checks
def failed_checks(expected):return {key:False for key in compare({},expected)}
def evaluate(selected=None):
    base=ROOT/'examples/evaluation';manifest=json.loads((base/'manifest.json').read_text())
    cfg=ai.credentials();results=[]
    for case in manifest['cases']:
        if selected and case['id'] not in selected:continue
        path=base/case['file'];started=time.monotonic();entry={'case':case['id'],'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        with tempfile.TemporaryDirectory() as temporary:
            try:
                pages=Path(temporary)/'pages';info=decode(path,pages)
                value,model=ai.extract(pages,info['page_count'],cfg)
                checks=compare(value,case['expected']);entry.update(status='valid_output',model=model,checks=checks,observed=value)
            except Exception as exc:
                entry.update(status='failed',error_code=exc.code if isinstance(exc,ai.ExtractionError) else type(exc).__name__,checks=failed_checks(case['expected']))
        entry['elapsed_seconds']=round(time.monotonic()-started,2)
        entry['matched']=sum(entry['checks'].values());entry['total']=len(entry['checks']);results.append(entry)
        print(case['id'],entry['status'],f"{entry['matched']}/{entry['total']}",flush=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':manifest['scope'],'model':cfg['model'],
            'method':'One attempt per case; no automatic retry. Exact normalized text and Decimal comparisons; missing values must remain null. Failed calls score zero for every expected field. Line order and page numbers are checked. Supplier identity is included.',
            'implementation_sha256':hashlib.sha256((ROOT/'app/ai.py').read_bytes()+(ROOT/'app/schemas.py').read_bytes()).hexdigest(),
            'summary':{'cases':len(results),'valid_outputs':sum(r['status']=='valid_output' for r in results),'matched_fields':sum(r['matched'] for r in results),'expected_fields':sum(r['total'] for r in results)},'results':results}
    target=ROOT/'docs/evaluations';target.mkdir(exist_ok=True);output=target/(stamp+'.json');output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report['summary']));print('Report:',output)
    return report
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true',help='Send the synthetic fixtures to Gemini; API charges may apply.');parser.add_argument('--case',action='append')
    args=parser.parse_args()
    if not args.live:parser.error('Pass --live to explicitly run paid-provider evaluation. No requests were sent.')
    evaluate(args.case)
