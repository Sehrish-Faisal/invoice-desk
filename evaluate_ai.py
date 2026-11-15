"""Explicit paid-provider smoke evaluation using synthetic fixtures only."""
import json
import tempfile
import time
from pathlib import Path
from decimal import Decimal
from app.ai import extract
from app.documents import decode
from app.config import ROOT
results=[]
for filename,number,qty,tax,total in [('DEMO-1042.png','DEMO-1042',20,0,2510),('DEMO-1043.pdf','DEMO-1043',8,100,1104)]:
    with tempfile.TemporaryDirectory() as temp:
        folder=Path(temp); metadata=decode(ROOT/'examples'/filename,folder/'pages'); start=time.monotonic()
        try:
            value,model=extract(folder/'pages',metadata['page_count'])
            expected={'invoice_number':number,'invoice_date':'2026-11-13','due_date':'2026-12-13','currency':'PKR','tax':str(tax),'discount':'0','declared_total':str(total)}
            checks={k:(Decimal(str(value[k]))==Decimal(v) if k in ('tax','discount','declared_total') and value[k] is not None else value[k]==v) for k,v in expected.items()}
            checks['quantity']=len(value['lines'])==1 and Decimal(value['lines'][0]['quantity'])==qty
            checks['unit_price']=len(value['lines'])==1 and Decimal(value['lines'][0]['unit_price'])==Decimal('125.50')
            results.append({'fixture':filename,'model':model,'elapsed_seconds':round(time.monotonic()-start,2),'checks':checks,'matched':sum(checks.values()),'total':len(checks)})
        except Exception as exc: results.append({'fixture':filename,'error':type(exc).__name__,'message':'Extraction failed; no accuracy result recorded.'})
report_path=ROOT/'docs/ai-evaluation.json'
previous=json.loads(report_path.read_text()) if report_path.exists() else None
report={'scope':'Two clean synthetic layouts only; nine selected fields per fixture, excluding supplier identity. Not a representative accuracy benchmark.','results':results}
if previous: report['previous_runs']=previous.get('previous_runs',[])+[{'scope':previous['scope'],'results':previous['results']}]
(ROOT/'docs/ai-evaluation.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
