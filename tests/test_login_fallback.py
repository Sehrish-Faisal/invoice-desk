from pathlib import Path
from html.parser import HTMLParser
from app.config import ROOT
from app import db
import manage
from conftest import sign_in

class LoginMarkup(HTMLParser):
    def __init__(self):super().__init__();self.form=None;self.submit=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='form' and attrs.get('id')=='login-form':self.form=attrs
        if tag=='button' and attrs.get('id')=='login-submit':self.submit=attrs
def test_script_failure_cannot_submit_password_in_url(client):
    parser=LoginMarkup();parser.feed(client.get('/').text)
    assert parser.form['method'].lower()=='post'
    assert parser.form['action']=='/api/auth/login'
    assert 'disabled' in parser.submit
    assert client.post('/api/auth/login',data={'username':'test','password':'sentinel'}).status_code==415
def test_module_mime_is_javascript(client):
    response=client.get('/draft.mjs')
    assert response.status_code==200
    assert response.headers['content-type'].split(';')[0]=='application/javascript'
def test_password_rotation_revokes_sessions_and_replaces_private_record(client,engine,seeded,tmp_path,monkeypatch):
    sign_in(client,seeded)
    monkeypatch.setattr(manage,'config',lambda:{'migration_url':engine.url.render_as_string(hide_password=False)})
    monkeypatch.setattr(manage,'STATE',tmp_path)
    path=tmp_path/'credentials.txt';path.write_text('operator: previous\napprover: retained\n')
    manage.reset_password('operator')
    assert client.get('/api/session').status_code==401
    text=path.read_text();assert 'previous' not in text and 'approver: retained' in text
    new_password=next(line.split(': ',1)[1] for line in text.splitlines() if line.startswith('operator:'))
    assert new_password!=seeded['password'] and len(new_password)>=24
    assert client.post('/api/auth/login',json={'username':'operator','password':seeded['password']}).status_code==401
    assert client.post('/api/auth/login',json={'username':'operator','password':new_password}).status_code==200
