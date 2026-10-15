# Xsens Outreach Manager

A local, free and open-source Streamlit application for individually personalized Microsoft 365 outreach. Your organization must already provide an Outlook mailbox; no paid hosting is required. Python 3.11–3.12 recommended. Secure Outlook sign-in is supported on Windows using DPAPI; contact import, drafts and automated tests also work on Linux.

## Install and run on Windows

From PowerShell in this checkout:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m streamlit run app.py --server.address 127.0.0.1
```

Open the local address printed by Streamlit. Keep the server bound to loopback; this app has no multi-user authentication. Run only one app instance for this data directory. Stop with Ctrl+C. For tests:

```powershell
.\.venv\Scripts\python -m pytest -q
```

Dependencies are free/open-source. Tests use a fake Outlook client and never send email. Linux development commands use `.venv/bin/python` instead.

## Microsoft Entra registration and OAuth

1. In the Microsoft Entra admin center, open **App registrations → New registration**. Name it Xsens Outreach Manager and select accounts in your organizational directory (single tenant).
2. Under **Authentication → Add a platform → Mobile and desktop applications**, register `http://localhost`. Enable public client flows if required by your tenant. MSAL opens a browser and uses a temporary loopback port. This is a public desktop client: **do not create a client secret**.
3. Under **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, add **Mail.Send**. Do not add application permissions. MSAL also requests standard OpenID/offline access scopes for sign-in and token renewal. Follow your administrator's consent policies; admin consent may be required.
4. Optionally add delegated **Mail.Read** only when you intend to use reply review and explicitly approve it in Settings. It is never requested by default. Mail.ReadWrite is not required: draft-only mode stores local drafts, not Outlook drafts.
5. Copy the Application (client) ID and Directory (tenant) ID into Settings. These identifiers are not secrets; optionally provide `XSENS_CLIENT_ID` and `XSENS_TENANT_ID` environment variables.
6. Select **Sign in with Microsoft**, use the intended mailbox account, and review the delegated consent screen. Re-sign in with the reply approval checkbox selected to enable optional Mail.Read.

The signed-in user's mailbox is used by `/me/sendMail`. Outlook integration requires HTTPS access to `login.microsoftonline.com` and `graph.microsoft.com`. Tenant conditional access, administrator restrictions, mailbox licensing, or consent policies may prevent sign-in/sending. Do not weaken organizational controls to resolve these failures. Reference: [desktop interactive authentication](https://learn.microsoft.com/entra/msal/python/getting-started/acquiring-tokens), [Graph sendMail](https://learn.microsoft.com/graph/api/user-sendmail), [Graph throttling](https://learn.microsoft.com/graph/throttling).

## Workflow

1. **Contacts:** upload `.xlsx` or `.csv` (first Excel sheet). Map source columns to first_name, last_name, company, email and role. Invalid addresses and duplicate file rows are excluded; missing fields are shown. Already stored emails are skipped without overwriting their history. Unmapped columns remain available as optional placeholder names; use word characters/underscores for their names.
2. **Templates:** save a subject and plain-text body with `{{first_name}}`, `{{company}}`, `{{role}}`, or optional fields. Missing values block campaign creation, so incomplete personalization cannot be silently sent.
3. **Campaigns:** filter by company/role and select individuals, or use Select all filtered contacts and the group checkbox. Preview the fully rendered text for each recipient. Save a draft campaign; no mail is sent.
4. Send a selected preview to your own test mailbox only after confirming the test action. A test really sends one email, but never uses campaign recipients or changes campaign history.
5. To send live mail, review all recipients/messages, turn off draft-only mode, confirm permission, and type `SEND`. Explicitly queue the drafts, then explicitly submit a bounded batch. The default is 5 emails/minute; maximum is 30/minute. Use lower limits if your organization requires them. Stop at the first submission failure. No background worker sends mail on startup or page rerun.
6. Submitted means Graph returned HTTP 202, **not delivered**. Track responses manually as Replied, Interested or Not Interested. Not Interested also adds suppression. Suppression blocks future campaigns and pending queue entries.
7. Optional reply review fetches up to 100 inbox messages from the chosen recent period, using Mail.Read only after approval and consent. Sender matching is only a candidate suggestion (not proof of a reply). Review and classify in Campaigns. Pagination, conversation threading and automatic classification are intentionally not implemented.
8. Dashboard exports contacts, templates, campaigns, outreach, audit events and suppression to Excel. Exported formula-like text is escaped to avoid spreadsheet formula execution.

## Duplicate prevention and failures

Every contact, campaign and outreach item has a UUID. SQLite enforces one recipient per campaign. A send is atomically claimed and committed before the HTTP request; the claim persists across crashes and page reruns. Claimed items cannot be automatically retried or requeued, even after a timeout. A crash may leave a claimed Queued item: reconcile it manually against Sent Items. The `x-xsens-outreach-id` header and client request ID aid investigation; Graph sendMail does **not** guarantee server-side idempotency.

HTTP failures including throttling stop the batch. Retry-After is recorded when present. Wait as directed, check Sent Items (and consult your administrator if necessary), then create a separately reviewed campaign only when you have verified that no mail was sent. That new campaign is a new intentional send; the app cannot prevent duplicates across separately created campaigns. Never blindly retry ambiguous submissions.

## Local security and data protection

SQLite records are stored in `%LOCALAPPDATA%\XsensOutreachManager\outreach.sqlite3`. MSAL's token cache is encrypted with Windows DPAPI under the same directory and bound to your Windows user. No token values or client secrets are stored in source files. Signing out removes the cached account; revoke consent separately in Microsoft/Entra if needed. Linux OAuth is blocked instead of falling back to plaintext token persistence.

SQLite and Excel exports contain personal information and are not encrypted by this app. Protect your Windows account, use BitLocker or another approved encrypted volume, restrict backups and exports, and follow organizational retention/deletion procedures. Close the application before backing up or deleting its data directory. Deleting the whole directory removes local history and suppression, so retain suppression according to your policy before deleting records. The app does not provide an automated retention policy or multi-user access control.

`.gitignore` excludes databases, tokens, logs, virtual environments and Streamlit secrets. Do not place contact lists/exports in the checkout or commit them. Never paste credentials into templates or environment scripts. Use lawful, relevant B2B outreach, honor opt-outs and suppression, identify yourself clearly, and comply with applicable privacy requirements and tenant sending limits. This application does not establish legal permission to send.

## Validation and scope

Automated tests cover Excel/CSV parsing, mapping, missing fields, duplicate detection, personalization, atomic draft creation, export escaping, suppression, concurrent send claiming, failed-send handling, and Streamlit page startup. Microsoft OAuth and actual Graph sending require a Windows machine, your Entra registration and deliberate user testing; development tests do not contact Outlook.
