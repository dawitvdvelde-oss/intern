from xsens.outlook import Outlook
import pytest

def test_graph_individual_recipient_and_202(monkeypatch):
    client=Outlook.__new__(Outlook)
    client.token=lambda:'fake-test-token'
    calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs))
        return type('Response',(),{'status_code':202})()
    monkeypatch.setattr('xsens.outlook.requests.post',post)
    client.send('ada@example.com','Hi','Personal body','unique-id')
    payload=calls[0][1]['json']['message']
    assert payload['toRecipients']==[{'emailAddress':{'address':'ada@example.com'}}]
    assert payload['internetMessageHeaders'][0]['value']=='unique-id'
    assert calls[0][1]['timeout']==30

def test_graph_throttling_no_retry(monkeypatch):
    client=Outlook.__new__(Outlook); client.token=lambda:'fake'
    calls=[]
    def post(*args,**kwargs):
        calls.append(1)
        return type('Response',(),{'status_code':429,'headers':{'Retry-After':'60'}})()
    monkeypatch.setattr('xsens.outlook.requests.post',post)
    with pytest.raises(RuntimeError,match='Retry-After=60'):
        client.send('ada@example.com','Hi','Body','id')
    assert len(calls)==1
