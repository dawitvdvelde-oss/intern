import os
from pathlib import Path
import time
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from filelock import FileLock

from xsens.core import Store, FIELDS, STATUSES, read_contacts, prepare_contacts, personalize
from xsens.outlook import Outlook, process_one

st.set_page_config(page_title='Xsens Outreach Manager', page_icon='✉️', layout='wide')
st.title('Xsens Outreach Manager')
st.caption('Personal outreach · Review every message · Keep a clear history')
data_root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'XsensOutreachManager'
store = Store(data_root / 'outreach.sqlite3')
page = st.sidebar.radio('Workspace', ['Dashboard','Contacts','Templates','Campaigns','Settings'])
st.sidebar.caption('Local records contain personal data. Protect this Windows account and your exports.')

def show_error(exc):
    st.error(str(exc))

def get_client():
    if not st.session_state.get('client'): raise ValueError('Configure and sign in on Settings first.')
    return st.session_state.client

if page == 'Dashboard':
    contacts, outreach = store.rows('contacts'), store.rows('outreach')
    cols = st.columns(4)
    for col, label, value in zip(cols, ['Contacts','Submitted emails','Replies / responses','Interested'], [len(contacts),sum(r['status']=='Submitted' for r in store.rows('events')),sum(r['status'] in ['Replied','Interested','Not Interested'] for r in outreach),sum(r['status']=='Interested' for r in outreach)]):
        col.metric(label,value)
    st.caption('Submitted means accepted by Microsoft Graph, not confirmed delivery. Reply counts include manually classified responses.')
    st.dataframe(pd.DataFrame(outreach),use_container_width=True)
    st.download_button('Export all records to Excel',store.export(),'xsens-outreach.xlsx')

elif page == 'Contacts':
    st.subheader('Import contacts')
    file = st.file_uploader('Excel or CSV',type=['xlsx','csv'])
    if file:
        try:
            frame = read_contacts(file,file.name)
            st.dataframe(frame.head(20),use_container_width=True)
            mapping = {}
            for field in FIELDS:
                options = ['(unmapped)'] + list(frame.columns)
                match = next((c for c in frame.columns if str(c).lower().replace(' ','_')==field),None)
                mapping[field] = st.selectbox(field,options,index=options.index(match) if match else 0)
            mapping = {k:None if v=='(unmapped)' else v for k,v in mapping.items()}
            if len([v for v in mapping.values() if v]) != len(set(v for v in mapping.values() if v)):
                st.error('Map each source column to only one contact field.')
            else:
                records,issues = prepare_contacts(frame,mapping)
                existing = {c['email'] for c in store.rows('contacts')}
                duplicate_count = sum(c['email'] in existing for c in records)
                st.info(f'{len(records)} valid unique file rows; {duplicate_count} already stored. Existing contacts are preserved. Unmapped columns become optional placeholders.')
                st.dataframe(pd.DataFrame(issues),use_container_width=True)
                if st.button('Import valid contacts',disabled=not records):
                    count = store.import_records(records)
                    st.success(f'Imported {count} contacts; {len(records)-count} existing contacts skipped.')
        except Exception as exc: show_error(exc)
    st.subheader('Contact directory')
    contacts = store.rows('contacts')
    search = st.text_input('Search company, role, name or email')
    st.dataframe(pd.DataFrame([c for c in contacts if search.lower() in str(c).lower()]),use_container_width=True)
    email = st.text_input('Suppress email from future outreach')
    if st.button('Add to suppression list'):
        try:
            store.suppress(email)
            st.success('Suppressed. This also blocks existing unsent queue entries.')
        except Exception as exc: show_error(exc)
    st.dataframe(pd.DataFrame(store.rows('suppression')),use_container_width=True)

elif page == 'Templates':
    st.info('Use {{first_name}}, {{company}}, {{role}}, or an unmapped column name. Missing placeholder values block campaign creation.')
    with st.form('template'):
        name = st.text_input('Template name')
        subject = st.text_input('Subject','Introduction to Xsens for {{company}}')
        body = st.text_area('Plain-text email','Hi {{first_name}},\n\nI would like to discuss how Xsens could support your work at {{company}}.\n\nBest regards',height=220)
        if st.form_submit_button('Save template'):
            if name.strip() and subject.strip() and body.strip():
                store.template(name,subject,body)
                st.success('Template saved')
            else: st.error('Name, subject and body are required.')
    st.dataframe(pd.DataFrame(store.rows('templates')),use_container_width=True)

elif page == 'Campaigns':
    st.subheader('Create a draft campaign')
    templates = store.rows('templates')
    contacts = [c for c in store.rows('contacts') if not c['suppressed']]
    if templates and contacts:
        template = st.selectbox('Template',templates,format_func=lambda t:t['name'])
        companies = st.multiselect('Companies (empty = all)',sorted(set(c['company'] for c in contacts)))
        role = st.text_input('Role contains')
        eligible = [c for c in contacts if (not companies or c['company'] in companies) and role.lower() in c['role'].lower()]
        chosen = st.multiselect('Recipients',eligible,format_func=lambda c:f"{c['email']} · {c['company']}")
        if st.button('Select all filtered contacts'):
            st.session_state['all_filtered'] = [c['id'] for c in eligible]
        use_filtered = st.checkbox('Use the selected filtered group',value=False)
        if use_filtered:
            chosen = [c for c in eligible if c['id'] in st.session_state.get('all_filtered',[])]
        if chosen:
            previews = []
            try:
                for c in chosen: previews.append({'email':c['email'],'subject':personalize(template['subject'],c),'body':personalize(template['body'],c)})
                st.dataframe(pd.DataFrame(previews),use_container_width=True)
                preview = st.selectbox('Read full personalized message',previews,format_func=lambda p:p['email'])
                st.text(preview['subject'] + '\n\n' + preview['body'])
                campaign_name = st.text_input('Campaign name')
                if st.button('Save draft campaign',disabled=not campaign_name.strip()):
                    store.campaign(campaign_name,chosen,template['subject'],template['body'])
                    st.success('Draft saved. No email was sent.')
            except Exception as exc: show_error(exc)
    else: st.info('Import contacts and create a template first.')
    campaigns = store.rows('campaigns')
    if campaigns:
        campaign = st.selectbox('Manage campaign',campaigns,format_func=lambda c:c['name'])
        rows = [r for r in store.rows('outreach') if r['campaign_id']==campaign['id']]
        contact_map = {c['id']:c for c in store.rows('contacts')}
        st.dataframe(pd.DataFrame([{**r,'email':contact_map[r['contact_id']]['email']} for r in rows]),use_container_width=True)
        message = st.selectbox('Review message / update response',rows,format_func=lambda r:contact_map[r['contact_id']]['email']+' · '+r['status'])
        st.text(message['subject']+'\n\n'+message['body'])
        response_status = st.selectbox('Response classification',['Replied','Interested','Not Interested'])
        if st.button('Record response'):
            store.status(message['id'],response_status,'Manually classified')
            if response_status=='Not Interested': store.suppress(contact_map[message['contact_id']]['email'])
            st.rerun()
        test_to = st.text_input('Test recipient (your own mailbox)')
        test_confirm = st.checkbox('I confirm this test sends one real email to my test mailbox')
        if st.button('Send selected preview as test',disabled=not test_confirm or not test_to):
            try:
                from email_validator import validate_email
                address = validate_email(test_to,check_deliverability=False).normalized
                get_client().send(address,'[TEST] '+message['subject'],message['body'],str(uuid.uuid4()))
                st.success('Test submitted to Graph. Campaign records were not changed.')
            except Exception as exc: show_error(exc)
        st.divider()
        draft_only = st.checkbox('Draft-only mode',value=True)
        rate = st.number_input('Maximum emails per minute',min_value=1,max_value=30,value=5)
        st.warning('Review every recipient and personalized message above. Sending is subject to your organization’s policies. Failed or uncertain submissions are never automatically retried.')
        confirmed = st.checkbox('I reviewed this campaign and have permission to contact these recipients')
        phrase = st.text_input('Type SEND to enable live submission')
        if st.button('Confirm and queue draft messages',disabled=draft_only or not confirmed or phrase!='SEND'):
            store.queue(campaign['id'])
            st.rerun()
        batch_size = st.number_input('Messages to submit this run',min_value=1,max_value=20,value=1)
        if st.button('Submit queued messages now',disabled=draft_only or not confirmed or phrase!='SEND'):
            try:
                client = get_client()
                client.token()  # Authenticate before claiming any row.
                with FileLock(str(data_root / 'send.lock'), timeout=0):
                    sent = 0
                    for _ in range(batch_size):
                        # Process-wide persistent timestamp limits spacing across reruns and restarts.
                        last = data_root / 'last-send.txt'
                        remaining = 60 / rate - (time.time()-float(last.read_text() if last.exists() else '0'))
                        if remaining>0: time.sleep(remaining)
                        last.write_text(str(time.time()))
                        outcome = process_one(store,client,campaign['id'])
                        if outcome is None: break
                        if outcome is False:
                            st.error('Submission failed. Batch stopped; inspect the record and Sent Items.')
                            break
                        sent += 1
                st.success(f'{sent} messages submitted. Refresh to view updated statuses.')
            except Exception as exc: show_error(exc)
        st.caption('To prevent duplicates, attempted messages cannot be requeued. Reconcile failures with Sent Items before making a separate, explicitly reviewed campaign.')

elif page == 'Settings':
    st.subheader('Microsoft 365 OAuth')
    client_id = st.text_input('Entra application (client) ID',value=os.environ.get('XSENS_CLIENT_ID',''))
    tenant_id = st.text_input('Directory (tenant) ID',value=os.environ.get('XSENS_TENANT_ID',''))
    read_approval = st.checkbox('I explicitly approve optional Mail.Read access for reply synchronization',value=False)
    if st.button('Sign in with Microsoft',disabled=not client_id or not tenant_id):
        try:
            client = Outlook(client_id,tenant_id,read_approval)
            result = client.sign_in()
            if 'access_token' in result:
                st.session_state.client = client
                st.session_state.read_approved = read_approval
                st.success('Signed in. Tokens are encrypted using Windows DPAPI.')
            else: st.error('Sign-in failed: '+result.get('error','Unknown OAuth error'))
        except Exception as exc: show_error(exc)
    if st.button('Sign out'):
        if st.session_state.get('client'): st.session_state.client.sign_out()
        st.session_state.pop('client',None)
        st.session_state.pop('read_approved',None)
        st.success('Account removed from token cache.')
    st.subheader('Optional reply review')
    st.caption('Fetch up to 100 recent inbox messages. Matches are suggestions by sender; review before marking as a reply. No automatic classification.')
    days = st.number_input('Look back (days)',min_value=1,max_value=30,value=7)
    if st.button('Fetch reply candidates',disabled=not read_approval or not st.session_state.get('read_approved')):
        try:
            since = (datetime.now(timezone.utc)-timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
            messages = get_client().replies(since)
            addresses = {c['email'] for c in store.rows('contacts')}
            st.session_state.reply_candidates = [{'sender':m.get('from',{}).get('emailAddress',{}).get('address',''),'subject':m.get('subject',''),'received':m.get('receivedDateTime','')} for m in messages if m.get('from',{}).get('emailAddress',{}).get('address','').lower() in addresses]
        except Exception as exc: show_error(exc)
    st.dataframe(pd.DataFrame(st.session_state.get('reply_candidates',[])),use_container_width=True)
    st.caption('Use Campaigns to record confirmed replies. Manage consent in Entra; signing out does not revoke permissions.')
store.db.close()
