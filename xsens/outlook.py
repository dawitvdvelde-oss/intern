import os
from pathlib import Path
import time
import uuid

import msal
import requests
from msal_extensions import FilePersistenceWithDataProtection, PersistedTokenCache

GRAPH = 'https://graph.microsoft.com/v1.0'

class Outlook:
    def __init__(self, client_id, tenant_id, read_mail=False):
        if os.name != 'nt':
            raise RuntimeError('Secure OAuth token persistence requires Windows DPAPI. Import, drafts and tests work on other platforms.')
        path = Path(os.environ['LOCALAPPDATA']) / 'XsensOutreachManager' / 'token-cache.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.cache = PersistedTokenCache(FilePersistenceWithDataProtection(str(path)))
        self.app = msal.PublicClientApplication(client_id, authority=f'https://login.microsoftonline.com/{tenant_id}', token_cache=self.cache)
        self.scopes = ['Mail.Send'] + (['Mail.Read'] if read_mail else [])
    def sign_in(self):
        return self.app.acquire_token_interactive(scopes=self.scopes, port=0, prompt='select_account')
    def token(self):
        accounts = self.app.get_accounts()
        result = self.app.acquire_token_silent(self.scopes, account=accounts[0]) if accounts else None
        if not result or 'access_token' not in result: raise RuntimeError('Sign in to Outlook first; consent may be required.')
        return result['access_token']
    def sign_out(self):
        for account in self.app.get_accounts(): self.app.remove_account(account)
    def send(self, email, subject, body, outreach_id):
        response = requests.post(GRAPH + '/me/sendMail', headers={'Authorization': 'Bearer ' + self.token(), 'client-request-id': outreach_id}, json={'message': {'subject':subject,'body':{'contentType':'Text','content':body},'toRecipients':[{'emailAddress':{'address':email}}], 'internetMessageHeaders':[{'name':'x-xsens-outreach-id','value':outreach_id}]}, 'saveToSentItems':True}, timeout=30)
        if response.status_code != 202:
            retry = response.headers.get('Retry-After', '')
            raise RuntimeError(f'Graph HTTP {response.status_code}; Retry-After={retry or "unspecified"}. No automatic retry; check Sent Items before reconciliation.')
    def replies(self, since):
        response = requests.get(GRAPH + '/me/mailFolders/inbox/messages', headers={'Authorization':'Bearer '+self.token()}, params={'$filter':f'receivedDateTime ge {since}', '$select':'id,subject,from,receivedDateTime,internetMessageHeaders', '$top':100}, timeout=30)
        response.raise_for_status()
        return response.json().get('value', [])

def process_one(store, client, cid):
    contacts = {r['id']:r for r in store.rows('contacts')}
    for row in store.rows('outreach'):
        if row['campaign_id'] != cid or row['status'] != 'Queued' or row['attempted']: continue
        contact = contacts[row['contact_id']]
        if contact['suppressed'] or store.is_suppressed(contact['email']):
            store.status(row['id'],'Failed','Suppressed before sending')
            continue
        if not store.claim(row['id']): continue
        try:
            client.send(contact['email'],row['subject'],row['body'],row['id'])
        except Exception as exc:
            store.status(row['id'],'Failed',str(exc))
            return False
        store.status(row['id'],'Submitted','Graph accepted request (202); delivery is not guaranteed')
        return True
    return None
