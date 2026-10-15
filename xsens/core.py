import io
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from email_validator import validate_email, EmailNotValidError

FIELDS = ['first_name', 'last_name', 'company', 'email', 'role']
STATUSES = ['Draft', 'Queued', 'Submitted', 'Failed', 'Replied', 'Interested', 'Not Interested']

def now():
    return datetime.now(timezone.utc).isoformat()

def read_contacts(file, name):
    return pd.read_csv(file, dtype=str).fillna('') if name.lower().endswith('.csv') else pd.read_excel(file, dtype=str).fillna('')

def prepare_contacts(frame, mapping):
    records, issues, seen = [], [], set()
    for index, source in frame.iterrows():
        row = {field: str(source.get(column, '')).strip() if column else '' for field, column in mapping.items()}
        extras = {str(k): str(v).strip() for k, v in source.items() if k not in mapping.values()}
        row['extra'] = extras
        try:
            row['email'] = validate_email(row.get('email', ''), check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            issues.append({'row': index + 2, 'issue': 'Invalid or missing email'})
            continue
        if row['email'] in seen:
            issues.append({'row': index + 2, 'issue': 'Duplicate email in file'})
            continue
        seen.add(row['email'])
        missing = [f for f in FIELDS if not row.get(f)]
        if missing:
            issues.append({'row': index + 2, 'issue': 'Missing: ' + ', '.join(missing)})
        records.append(row)
    return records, issues

def personalize(text, contact):
    values = {**contact.get('extra', {}), **contact}
    def replace(match):
        key = match.group(1)
        if key not in values or values[key] is None or str(values[key]).strip() == '':
            raise ValueError(f'Missing placeholder value: {key}')
        return str(values[key])
    return re.sub(r'{{\s*([\w]+)\s*}}', replace, text)

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, first_name TEXT, last_name TEXT, company TEXT, role TEXT, extra TEXT, suppressed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY, name TEXT, subject TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY, name TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS outreach(id TEXT PRIMARY KEY, campaign_id TEXT REFERENCES campaigns(id), contact_id TEXT REFERENCES contacts(id), subject TEXT, body TEXT, status TEXT, created TEXT, updated TEXT, attempted INTEGER DEFAULT 0, error TEXT DEFAULT '', UNIQUE(campaign_id,contact_id));
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, outreach_id TEXT REFERENCES outreach(id), status TEXT, created TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS suppression(email TEXT PRIMARY KEY);
        ''')
        self.db.commit()
    def rows(self, table):
        if table not in ['contacts', 'templates', 'campaigns', 'outreach', 'events', 'suppression']:
            raise ValueError('Unknown table')
        result = [dict(r) for r in self.db.execute(f'SELECT * FROM {table}')]
        for r in result:
            if 'extra' in r: r['extra'] = json.loads(r['extra'])
        return result
    def import_records(self, records):
        added = 0
        with self.db:
            for r in records:
                cur = self.db.execute('INSERT OR IGNORE INTO contacts VALUES(?,?,?,?,?,?,?,?)', (str(uuid.uuid4()), r['email'], r.get('first_name',''), r.get('last_name',''), r.get('company',''), r.get('role',''), json.dumps(r.get('extra',{})), int(self.is_suppressed(r['email']))))
                added += cur.rowcount
        return added
    def is_suppressed(self, email):
        return self.db.execute('SELECT 1 FROM suppression WHERE email=?', (email.lower(),)).fetchone() is not None
    def suppress(self, email):
        email = validate_email(email, check_deliverability=False).normalized.lower()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO suppression VALUES(?)', (email,))
            self.db.execute('UPDATE contacts SET suppressed=1 WHERE email=?', (email,))
    def template(self, name, subject, body):
        with self.db:
            self.db.execute('INSERT INTO templates VALUES(?,?,?,?)', (str(uuid.uuid4()), name, subject, body))
    def campaign(self, name, contacts, subject, body):
        # Render everything before committing, so an invalid placeholder creates no partial campaign.
        rendered = [(c, personalize(subject,c), personalize(body,c)) for c in contacts if not c['suppressed'] and not self.is_suppressed(c['email'])]
        if not rendered: raise ValueError('No eligible recipients')
        cid = str(uuid.uuid4())
        with self.db:
            self.db.execute('INSERT INTO campaigns VALUES(?,?,?)', (cid,name,now()))
            for c,s,b in rendered:
                oid = str(uuid.uuid4())
                self.db.execute('INSERT INTO outreach(id,campaign_id,contact_id,subject,body,status,created,updated) VALUES(?,?,?,?,?,?,?,?)', (oid,cid,c['id'],s,b,'Draft',now(),now()))
                self.db.execute('INSERT INTO events VALUES(?,?,?,?,?)', (str(uuid.uuid4()),oid,'Draft',now(),'Created'))
        return cid
    def status(self, oid, status, detail=''):
        if status not in STATUSES: raise ValueError('Invalid status')
        with self.db:
            self.db.execute('UPDATE outreach SET status=?,updated=?,error=? WHERE id=?', (status,now(),detail,oid))
            self.db.execute('INSERT INTO events VALUES(?,?,?,?,?)', (str(uuid.uuid4()),oid,status,now(),detail))
    def queue(self, cid):
        with self.db:
            ids = [r[0] for r in self.db.execute("SELECT id FROM outreach WHERE campaign_id=? AND status='Draft' AND attempted=0",(cid,))]
        for oid in ids: self.status(oid,'Queued','Explicitly confirmed')
    def claim(self, oid):
        # Commit before Graph request. Crash/timeout after this point requires manual reconciliation.
        with self.db:
            return self.db.execute("UPDATE outreach SET attempted=1,updated=? WHERE id=? AND status='Queued' AND attempted=0", (now(),oid)).rowcount == 1
    def export(self):
        result = io.BytesIO()
        with pd.ExcelWriter(result, engine='openpyxl') as writer:
            for table in ['contacts','templates','campaigns','outreach','events','suppression']:
                rows = self.rows(table)
                for r in rows:
                    if 'extra' in r: r['extra'] = json.dumps(r['extra'])
                    for k,v in r.items():
                        if isinstance(v,str) and v.startswith(('=','+','-','@')): r[k] = "'" + v
                pd.DataFrame(rows).to_excel(writer,sheet_name=table,index=False)
        return result.getvalue()
