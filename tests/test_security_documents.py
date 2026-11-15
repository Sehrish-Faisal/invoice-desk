import io
from PIL import Image
import pytest
from conftest import sign_in
from app import db
from app.config import config
from sqlalchemy.exc import DBAPIError

def png():
    data=io.BytesIO(); Image.new('RGB',(100,100),'white').save(data,format='PNG'); return data.getvalue()
def test_auth_csrf_origin_and_cookie(client,seeded):
    assert client.get('/api/invoices').status_code==401
    response=client.post('/api/auth/login',json={'username':'operator','password':seeded['password']})
    cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'max-age=3600' in cookie
    sign_in(client,seeded)
    client.headers.pop('x-csrf-token')
    assert client.post('/api/auth/logout',json={}).status_code==403
    client.headers['origin']='https://evil.example'
    assert client.post('/api/auth/login',json={'username':'operator','password':seeded['password']}).status_code==403
    assert client.get('/health',headers={'host':'evil.example'}).status_code==400
def test_role_and_validation_error_redaction(client,seeded):
    sign_in(client,seeded)
    assert client.post('/api/suppliers',json={'name':'Invalid access'}).status_code==403
    r=client.post('/api/auth/login',json={'username':'bad spaces','password':'SECRET_SENTINEL'})
    assert r.status_code==422 and 'SECRET_SENTINEL' not in r.text
def test_login_throttle(client,seeded):
    for _ in range(5): assert client.post('/api/auth/login',json={'username':'operator','password':'incorrect'}).status_code==401
    assert client.post('/api/auth/login',json={'username':'operator','password':seeded['password']}).status_code==429
def test_upload_duplicate_preview_and_bad_file(client,seeded):
    sign_in(client,seeded)
    r=client.post('/api/invoices',files={'file':('../../evil.png',png(),'image/png')},data={'consent':'false'}); assert r.status_code==200,r.text
    ident=r.json()['id']; preview=client.get(f'/api/invoices/{ident}/pages/1'); assert preview.status_code==200 and preview.headers['content-type']=='image/png'
    assert client.get(f'/api/invoices/{ident}/pages/0').status_code==404
    assert client.post('/api/invoices',files={'file':('other.png',png(),'image/png')}).status_code==409
    assert client.post('/api/invoices',files={'file':('attack.pdf',b'<script>alert(1)</script>','application/pdf')}).status_code==422
    client.cookies.clear(); assert client.get(f'/api/invoices/{ident}/pages/1').status_code==401
def test_upload_body_limit(client,seeded):
    sign_in(client,seeded)
    assert client.post('/api/invoices',files={'file':('large.png',b'a'*(10*1024*1024+1),'image/png')}).status_code==413
def test_security_headers(client):
    r=client.get('/')
    assert r.headers['x-frame-options']=='DENY' and "script-src 'self'" in r.headers['content-security-policy']
    assert r.headers['cache-control']=='no-store' and r.headers['x-content-type-options']=='nosniff'
def test_expired_session_rejected(client,seeded,engine):
    sign_in(client,seeded)
    with engine.begin() as c:db.run(c,"UPDATE sessions SET expires_at=now()-interval '1 second'")
    assert client.get('/api/session').status_code==401
def test_pdf_page_limit(client,seeded):
    sign_in(client,seeded); output=io.BytesIO(); image=Image.new('RGB',(20,20),'white')
    image.save(output,format='PDF',save_all=True,append_images=[image]*10)
    assert client.post('/api/invoices',files={'file':('too-many.pdf',output.getvalue(),'application/pdf')}).status_code==422
def test_pdf_and_corrupt_image(client,seeded):
    sign_in(client,seeded); output=io.BytesIO();Image.new('RGB',(20,20),'white').save(output,format='PDF')
    assert client.post('/api/invoices',files={'file':('valid.pdf',output.getvalue(),'application/pdf')}).status_code==200
    assert client.post('/api/invoices',files={'file':('corrupt.png',b'\x89PNG\r\n\x1a\ninvalid','image/png')}).status_code==422
def test_runtime_role_is_restricted():
    e=db.engine(config()['database_url'])
    try:
        for sql in ['CREATE TABLE forbidden(id integer)','DELETE FROM audit_events WHERE false','DELETE FROM users WHERE false','UPDATE exports SET csv=csv WHERE false']:
            with pytest.raises(DBAPIError):
                with e.begin() as c: db.run(c,sql)
    finally:e.dispose()
