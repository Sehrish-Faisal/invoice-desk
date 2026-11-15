from uuid import uuid4
import pytest
from app import db,notifications,services,schemas
from conftest import invoice,review_data,sign_in

def contact(engine,seeded,name='operator'):
    with engine.begin() as c:
        db.run(c,'INSERT INTO notification_contacts(user_id,email) VALUES(:u,:e)',u=seeded['users'][name]['id'],e=name+'@example.com')

def emit(engine,ident,action='extraction_done'):
    with engine.begin() as c: db.audit(c,None,action,ident)

def test_recipients_follow_separation_of_duties(engine,seeded):
    ident=invoice(engine,seeded)
    services.save_review(engine,ident,schemas.ReviewRequest(**review_data(seeded)),seeded['users']['operator'])
    services.submit(engine,ident,1,seeded['users']['operator'])
    with engine.connect() as c:
        rows=db.rows(c,'SELECT user_id FROM notifications')
        assert {r['user_id'] for r in rows}=={seeded['users'][n]['id'] for n in ['admin','approver']}
    services.decide(engine,ident,schemas.Decision(expected_version=2,decision='approve',reason='Checked source'),seeded['users']['approver'])
    with engine.connect() as c:
        assert db.run(c,"SELECT count(*) FROM notifications WHERE user_id=:u AND title='Invoice approved'",u=seeded['users']['operator']['id']).scalar_one()==1

def test_inbox_private_reads_and_csrf(client,engine,seeded):
    ident=invoice(engine,seeded);emit(engine,ident)
    assert client.get('/api/notifications').status_code==401
    sign_in(client,seeded)
    result=client.get('/api/notifications').json();assert result['unread']==1
    item=result['items'][0]['id']
    token=client.headers.pop('x-csrf-token')
    assert client.post('/api/notifications/read-all',json={}).status_code==403
    client.headers['x-csrf-token']=token
    sign_in(client,seeded,'approver')
    assert client.get('/api/notifications').json()['total']==0
    assert client.post('/api/notifications/'+item+'/read',json={}).status_code==404
    sign_in(client,seeded)
    assert client.post('/api/notifications/'+item+'/read',json={}).status_code==200
    assert client.get('/api/notifications').json()['unread']==0

def test_notification_rollback_and_event_dedup(engine,seeded):
    ident=invoice(engine,seeded)
    with pytest.raises(RuntimeError):
        with engine.begin() as c: db.audit(c,None,'extraction_done',ident);raise RuntimeError()
    with engine.begin() as c:
        assert db.run(c,'SELECT count(*) FROM notifications').scalar_one()==0
        db.audit(c,None,'extraction_done',ident)
        event=db.run(c,'SELECT max(id) FROM audit_events').scalar_one()
        notifications.record_event(c,event,'extraction_done',ident)
        assert db.run(c,'SELECT count(*) FROM notifications').scalar_one()==1

def test_preferences_cancel_queued_mail(client,engine,seeded):
    contact(engine,seeded);ident=invoice(engine,seeded);emit(engine,ident);sign_in(client,seeded)
    assert client.put('/api/notifications/preferences',json={'email_enabled':False}).status_code==200
    with engine.connect() as c: assert db.run(c,'SELECT status FROM notification_emails').scalar_one()=='cancelled'
    emit(engine,ident,'extraction_failed')
    with engine.connect() as c: assert db.run(c,'SELECT count(*) FROM notification_emails').scalar_one()==1
    assert client.put('/api/notifications/preferences',json={'email_enabled':'true'}).status_code==422

def test_no_contact_no_mail_or_self_enrolment(client,engine,seeded):
    ident=invoice(engine,seeded);emit(engine,ident);sign_in(client,seeded)
    assert client.get('/api/notifications/preferences').json()['email'] is None
    assert client.put('/api/notifications/preferences',json={'email_enabled':True}).status_code==409
    with engine.connect() as c: assert db.run(c,'SELECT count(*) FROM notification_emails').scalar_one()==0

def test_delivery_success_does_not_repeat(engine,seeded,monkeypatch):
    contact(engine,seeded);ident=invoice(engine,seeded);emit(engine,ident)
    monkeypatch.setattr(notifications,'settings',lambda:{'configured':True})
    calls=[];monkeypatch.setattr(notifications,'deliver',lambda *args:calls.append(args))
    assert notifications.send_once(engine)
    assert not notifications.send_once(engine)
    assert len(calls)==1
    with engine.connect() as c: assert db.run(c,'SELECT status FROM notification_emails').scalar_one()=='sent'

def test_failed_delivery_bounded_and_sanitized(engine,seeded,monkeypatch):
    contact(engine,seeded);ident=invoice(engine,seeded);emit(engine,ident)
    monkeypatch.setattr(notifications,'settings',lambda:{'configured':True})
    def fail(*args): raise RuntimeError('SECRET password provider reply')
    monkeypatch.setattr(notifications,'deliver',fail)
    for attempt in range(3):
        assert notifications.send_once(engine)
        with engine.begin() as c:
            r=db.one(c,'SELECT * FROM notification_emails');assert r['attempts']==attempt+1
            assert 'SECRET' not in r['error']
            assert r['status']==('failed' if attempt==2 else 'queued')
            db.run(c,"UPDATE notification_emails SET available_at=now()-interval '1 second'")
    assert not notifications.send_once(engine)

def test_no_smtp_keeps_queue_and_inactive_user_cancelled(engine,seeded,monkeypatch):
    contact(engine,seeded);ident=invoice(engine,seeded);emit(engine,ident)
    monkeypatch.setattr(notifications,'settings',lambda:None)
    assert not notifications.send_once(engine)
    with engine.begin() as c:
        assert db.run(c,'SELECT attempts FROM notification_emails').scalar_one()==0
        db.run(c,'UPDATE users SET active=false WHERE id=:u',u=seeded['users']['operator']['id'])
    monkeypatch.setattr(notifications,'settings',lambda:{'configured':True})
    monkeypatch.setattr(notifications,'deliver',lambda *args:pytest.fail('Inactive recipient must not receive mail'))
    assert notifications.send_once(engine)
    with engine.connect() as c: assert db.run(c,'SELECT status FROM notification_emails').scalar_one()=='cancelled'

def test_expired_lease_is_reclaimed(engine,seeded):
    contact(engine,seeded);ident=invoice(engine,seeded);emit(engine,ident)
    first=notifications.claim_email(engine);assert first
    assert notifications.claim_email(engine) is None
    with engine.begin() as c: db.run(c,"UPDATE notification_emails SET lease_until=now()-interval '1 second'")
    second=notifications.claim_email(engine)
    assert second['lease_token']!=first['lease_token'] and second['attempts']==2

@pytest.mark.parametrize('value',['a@example.com\r\nBcc: bad@example.com','a b@example.com','not-an-email','x@localhost'])
def test_reject_header_injection(value): assert not notifications.valid_email(value)

def test_smtp_requires_tls():
    with pytest.raises(ValueError): notifications.validate_settings({'host':'smtp.example.com','port':25,'mode':'plain','sender':'a@example.com','username':'a','password':'secret'})

def test_paging_and_mark_all_are_recipient_scoped(client,engine,seeded):
    ident=invoice(engine,seeded)
    for _ in range(31): emit(engine,ident)
    emit(engine,ident,'submitted')
    sign_in(client,seeded)
    first=client.get('/api/notifications').json()
    second=client.get('/api/notifications?offset=30').json()
    assert len(first['items'])==30 and len(second['items'])==1
    assert first['total']==31 and first['unread']==31
    assert client.post('/api/notifications/read-all',json={}).status_code==200
    assert client.get('/api/notifications').json()['unread']==0
    sign_in(client,seeded,'approver')
    assert client.get('/api/notifications').json()['unread']==1

def test_runtime_cannot_replace_email_addresses(engine):
    with engine.connect() as c:
        assert not db.run(c,"SELECT has_column_privilege('invoicedesk_app','notification_contacts','email','UPDATE')").scalar_one()
        assert not db.run(c,"SELECT has_table_privilege('invoicedesk_app','notification_contacts','INSERT')").scalar_one()
        assert db.run(c,"SELECT has_column_privilege('invoicedesk_app','notification_contacts','email_enabled','UPDATE')").scalar_one()

def test_smtp_message_and_tls(monkeypatch):
    calls=[]
    class SMTP:
        def __init__(self,*a,**kw): calls.append(('connect',a,kw))
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def ehlo(self): calls.append('ehlo')
        def starttls(self,**kw): calls.append('tls');assert kw['context'].check_hostname
        def login(self,*a): calls.append('login')
        def send_message(self,message):
            calls.append('sent');assert 'invoice details' not in message.get_content()
            assert message['To']=='staff@example.com'
            assert '127.0.0.1' not in message.get_content()
    monkeypatch.setattr(notifications.smtplib,'SMTP',SMTP)
    notifications.deliver({'host':'smtp.example.com','port':587,'mode':'starttls','sender':'sender@example.com','username':'sender','password':'private'}, {'email':'staff@example.com','title':'Invoice approved'}, {'notification_id':uuid4()})
    assert calls[1:]==['ehlo','tls','ehlo','login','sent']
