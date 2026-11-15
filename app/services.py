import csv
import io
import json
from decimal import Decimal
from uuid import uuid4
from . import db
from .schemas import Review, total
from .security import fail, require

def locked(c,ident,version=None):
    inv=db.one(c,'SELECT * FROM invoices WHERE id=:id FOR UPDATE',id=ident)
    if not inv: fail(404,'Invoice not found.')
    if version is not None and inv['version']!=version: fail(409,'This invoice changed. Reload before continuing.')
    return inv
def issues(c,review,lock=False):
    problems=[]
    po=db.one(c,'SELECT * FROM purchase_orders WHERE id=:i',i=review.po_id)
    if not po or po['supplier_id']!=review.supplier_id: problems.append('Purchase order does not belong to this supplier.')
    if po and po['currency']!=review.currency: problems.append('Currency does not match the purchase order.')
    lines=db.rows(c,'SELECT * FROM po_lines WHERE po_id=:i ORDER BY id'+(' FOR UPDATE' if lock else ''),i=review.po_id)
    byid={r['id']:r for r in lines}; requested={}
    for line in review.lines:
        pl=byid.get(line.po_line_id)
        if not pl: problems.append('Select a purchase order line for every invoice line.'); continue
        if line.unit_price!=pl['unit_price']: problems.append('Unit price does not match the purchase order: '+pl['description'])
        requested[line.po_line_id]=requested.get(line.po_line_id,Decimal(0))+line.quantity
    for ident,qty in requested.items():
        used=db.run(c,'SELECT COALESCE(sum(quantity),0) FROM allocations WHERE po_line_id=:i',i=ident).scalar_one()
        if used+qty>byid[ident]['quantity']: problems.append('Quantity exceeds the unbilled purchase order balance: '+byid[ident]['description'])
    if total(review)<0 or abs(total(review)-review.declared_total)>Decimal('.01'): problems.append('Calculated total does not match the declared total within 0.01.')
    return list(dict.fromkeys(problems))
def save_review(engine,ident,body,user):
    require(user,'operator','admin')
    review=Review.model_validate(body.model_dump(exclude={'expected_version'})); value=review.model_dump_json()
    with engine.begin() as c:
        inv=locked(c,ident,body.expected_version)
        if inv['state'] not in ('needs_review','rejected'): fail(409,'Only invoices awaiting review can be edited.')
        if not db.one(c,'SELECT id FROM suppliers WHERE id=:i',i=review.supplier_id): fail(422,'Supplier not found.')
        # Serialized by invoice lock; global uniqueness resolves concurrent business duplicates.
        db.run(c,'DELETE FROM invoice_numbers WHERE invoice_id=:i',i=ident)
        number=''.join(review.invoice_number.upper().split())
        db.run(c,'INSERT INTO invoice_numbers VALUES(:s,:n,:i)',s=review.supplier_id,n=number,i=ident)
        version=inv['version']+1
        db.run(c,"UPDATE invoices SET review=CAST(:r AS jsonb),version=:v,reviewer_id=:u,state='needs_review' WHERE id=:i",r=value,v=version,u=user['id'],i=ident)
        db.run(c,'INSERT INTO invoice_versions(invoice_id,version,actor_id,review) VALUES(:i,:v,:u,CAST(:r AS jsonb))',i=ident,v=version,u=user['id'],r=value)
        db.audit(c,user['id'],'review_saved',ident,version=version)
        return {'version':version,'issues':issues(c,review),'calculated_total':str(total(review))}
def submit(engine,ident,version,user):
    require(user,'operator','admin')
    with engine.begin() as c:
        inv=locked(c,ident,version)
        if inv['state']!='needs_review' or not inv['review']: fail(409,'Save a review before submitting.')
        problems=issues(c,Review.model_validate(inv['review']))
        if problems: fail(409,' '.join(problems))
        db.run(c,"UPDATE invoices SET state='submitted',version=version+1 WHERE id=:i",i=ident)
        db.audit(c,user['id'],'submitted',ident,version=version+1)
    return {'state':'submitted','version':version+1}
def decide(engine,ident,body,user):
    require(user,'approver','admin')
    with engine.begin() as c:
        inv=locked(c,ident,body.expected_version)
        if inv['state']!='submitted': fail(409,'Only submitted invoices can receive a decision.')
        if user['id'] in (inv['uploader_id'],inv['reviewer_id']): fail(403,'An independent person must approve or reject this invoice.')
        state='approved' if body.decision=='approve' else 'rejected'
        if state=='approved':
            review=Review.model_validate(inv['review']); problems=issues(c,review,lock=True)
            if problems: fail(409,' '.join(problems))
            for i,line in enumerate(review.lines):
                db.run(c,'INSERT INTO allocations VALUES(:i,:n,:p,:q)',i=ident,n=i,p=line.po_line_id,q=line.quantity)
        db.run(c,'UPDATE invoices SET state=:s,version=version+1 WHERE id=:i',s=state,i=ident)
        db.audit(c,user['id'],state,ident,version=inv['version']+1,reason=body.reason)
    return {'state':state}
def safe_cell(value):
    s=str(value)
    return "'"+s if s.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else s
def export_invoice(engine,ident,user):
    require(user,'admin','approver')
    with engine.begin() as c:
        inv=locked(c,ident)
        existing=db.one(c,'SELECT * FROM exports WHERE invoice_id=:i',i=ident)
        if existing: return dict(existing)
        if inv['state']!='approved': fail(409,'Only approved invoices can be exported.')
        r=Review.model_validate(inv['review']); out=io.StringIO(newline=''); writer=csv.writer(out)
        writer.writerow(['invoice_id','invoice_number','supplier_id','invoice_date','due_date','currency','subtotal','tax','discount','total'])
        writer.writerow([safe_cell(v) for v in [ident,r.invoice_number,r.supplier_id,r.invoice_date,r.due_date,r.currency,total(r)-r.tax+r.discount,r.tax,r.discount,total(r)]])
        export_id=uuid4(); content=out.getvalue()
        db.run(c,'INSERT INTO exports(id,invoice_id,csv) VALUES(:e,:i,:v)',e=export_id,i=ident,v=content)
        db.run(c,"UPDATE invoices SET state='exported',version=version+1 WHERE id=:i",i=ident)
        db.audit(c,user['id'],'exported',ident,batch_id=str(export_id))
        return {'id':export_id,'csv':content}
