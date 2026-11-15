"""Generic server-side connector example. Uses a key from environment or hidden prompt.

Run with the InvoiceDesk Python environment; HTTP is accepted only for loopback.
Import files contain {"records": [...]} and default to validation without writes.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import httpx

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8030')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('status')
    importing=sub.add_parser('import');importing.add_argument('kind',choices=['suppliers','purchase_orders']);importing.add_argument('file',type=Path);importing.add_argument('--commit',action='store_true')
    invoices=sub.add_parser('invoices');invoices.add_argument('--offset',type=int,default=0)
    args=parser.parse_args();url=urlsplit(args.base_url)
    if url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        parser.error('Use an origin URL without credentials, path, query or fragment.')
    if url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('127.0.0.1','localhost')):
        parser.error('Remote connections require HTTPS.')
    key=os.environ.get('INVOICEDESK_API_KEY') or getpass.getpass('Integration API key (hidden): ')
    try:
        with httpx.Client(base_url=args.base_url.rstrip('/')+'/api/integration/v1/',headers={'Authorization':'Bearer '+key},timeout=20,follow_redirects=False,trust_env=False) as client:
            if args.command=='status': response=client.get('status')
            elif args.command=='invoices': response=client.get('invoices',params={'offset':args.offset})
            else:
                if args.file.stat().st_size>100000: raise ValueError('Use an import file up to 100 KB.')
                data=json.loads(args.file.read_text(encoding='utf-8-sig'));data['dry_run']=not args.commit
                response=client.post('import/'+args.kind,json=data)
            if response.status_code>=300:
                print('Request failed (HTTP '+str(response.status_code)+'). Check the integration guide and settings.');return 1
            print(json.dumps(response.json(),indent=2));return 0
    except (httpx.HTTPError,ValueError,OSError):
        print('Connection or input failed. Check the URL, file, TLS and network; credentials were not printed.');return 1

if __name__=='__main__': raise SystemExit(main())
