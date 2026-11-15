import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get('INVOICEDESK_STATE', str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'InvoiceDesk')))
def config():
    p = STATE / 'config.json'
    return json.loads(p.read_text()) if p.exists() else {}
def database_url():
    return os.environ.get('INVOICEDESK_DATABASE_URL') or config().get('database_url', '')
PORT = 8030
MAX_UPLOAD = 10 * 1024 * 1024
