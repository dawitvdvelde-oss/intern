import io
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest
from xsens.core import Store, prepare_contacts, personalize, read_contacts
from xsens.outlook import process_one

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'test.sqlite3')

def records():
    return [{'first_name':'Ada','last_name':'Lovelace','company':'Example','email':'ada@example.com','role':'Engineer','extra':{'sector':'Motion'}}]

def test_import_validation_and_mapping():
    df = pd.DataFrame({'Given':['Ada','Other','Bad'],'Mail':['ada@example.com','ADA@example.com','bad']})
    parsed,issues = prepare_contacts(df,{'first_name':'Given','email':'Mail','company':None})
    assert len(parsed)==1
    assert any('Duplicate' in i['issue'] for i in issues)
    assert any('Invalid' in i['issue'] for i in issues)
    assert any('Missing' in i['issue'] for i in issues)

def test_excel_csv_and_optional_fields():
    df=pd.DataFrame(records()).drop(columns='extra')
    excel=io.BytesIO()
    df.to_excel(excel,index=False)
    excel.seek(0)
    assert read_contacts(excel,'contacts.xlsx').iloc[0]['first_name']=='Ada'
    assert read_contacts(io.StringIO(df.to_csv(index=False)),'contacts.csv').iloc[0]['email']=='ada@example.com'
    assert personalize('Hi {{ first_name }} at {{company}}: {{sector}}',records()[0])=='Hi Ada at Example: Motion'
    with pytest.raises(ValueError): personalize('{{unknown}}',records()[0])

def test_duplicates_atomic_drafts_export(store):
    assert store.import_records(records())==1
    assert store.import_records(records())==0
    with pytest.raises(ValueError): store.campaign('bad',store.rows('contacts'),'{{missing}}','body')
    assert not store.rows('campaigns')
    store.campaign('good',store.rows('contacts'),'Hello {{first_name}}','Body')
    assert store.rows('outreach')[0]['status']=='Draft'
    assert len(pd.read_excel(io.BytesIO(store.export()),sheet_name='contacts'))==1

class Fake:
    def __init__(self,fail=False): self.calls=[]; self.fail=fail
    def send(self,*args):
        self.calls.append(args)
        if self.fail: raise TimeoutError('Uncertain timeout')

def campaign(store):
    store.import_records(records())
    cid=store.campaign('c',store.rows('contacts'),'Hi','Body')
    store.queue(cid)
    return cid

def test_one_send_and_no_duplicate(store):
    cid=campaign(store); client=Fake()
    assert process_one(store,client,cid) is True
    assert process_one(store,client,cid) is None
    store.queue(cid)
    assert process_one(store,client,cid) is None
    assert len(client.calls)==1
    assert store.rows('outreach')[0]['status']=='Submitted'

def test_uncertain_send_never_retried(store):
    cid=campaign(store); client=Fake(True)
    assert process_one(store,client,cid) is False
    assert process_one(store,client,cid) is None
    assert store.rows('outreach')[0]['attempted']==1
    assert store.rows('outreach')[0]['status']=='Failed'

def test_suppression_blocks_existing_queue(store):
    cid=campaign(store); client=Fake()
    store.suppress('ada@example.com')
    assert process_one(store,client,cid) is None
    assert not client.calls
    with pytest.raises(ValueError): store.campaign('suppressed',store.rows('contacts'),'Hi','body')

def test_atomic_claim_multiple_connections(tmp_path):
    path=tmp_path/'shared.db'; s=Store(path); campaign(s)
    oid=s.rows('outreach')[0]['id']; s.db.close()
    def claim(_):
        local=Store(path)
        try: return local.claim(oid)
        finally: local.db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(claim,range(2)))==1

def test_formula_export_safety(store):
    r=records()[0]; r['company']='=1+1'; store.import_records([r])
    df=pd.read_excel(io.BytesIO(store.export()),sheet_name='contacts')
    assert df.iloc[0]['company']=="'=1+1"
