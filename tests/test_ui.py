from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_dashboard_and_pages(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    app=AppTest.from_file(Path(__file__).parents[1] / 'app.py').run()
    assert not app.exception
    assert app.title[0].value=='Xsens Outreach Manager'
    for page in ['Contacts','Templates','Campaigns','Settings']:
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception

def test_draft_campaign_review_defaults(tmp_path,monkeypatch):
    from xsens.core import Store
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    store=Store(tmp_path/'XsensOutreachManager'/'outreach.sqlite3')
    store.import_records([{'email':'ada@example.com','first_name':'Ada','company':'Example','role':'Engineer'}])
    store.template('Intro','Hello {{first_name}}','For {{company}}')
    store.campaign('Draft',store.rows('contacts'),'Hello {{first_name}}','For {{company}}')
    store.db.close()
    app=AppTest.from_file(Path(__file__).parents[1]/'app.py').run()
    app.sidebar.radio[0].set_value('Campaigns').run()
    assert not app.exception
    assert any(c.label=='Draft-only mode' and c.value for c in app.checkbox)
    for button in app.button:
        if button.label in ['Submit queued messages now','Confirm and queue draft messages','Send selected preview as test']:
            assert button.disabled
    store=Store(tmp_path/'XsensOutreachManager'/'outreach.sqlite3')
    assert store.rows('outreach')[0]['status']=='Draft'
    assert store.rows('outreach')[0]['attempted']==0
    store.db.close()
