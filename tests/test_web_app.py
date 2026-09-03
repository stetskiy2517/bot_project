import unittest
from unittest.mock import patch
import web_app
from core.db import get_or_create_google_user

class WebAppTests(unittest.TestCase):
    def setUp(self): self.app=web_app.create_web_app();self.client=self.app.test_client();web_app._user_state.clear()
    def _google_session(self,client,sub,email,name):
        uid=get_or_create_google_user(sub,email,name)
        with client.session_transaction() as s:s["user_id"]=uid
        return uid
    def test_health_does_not_require_telegram(self): self.assertEqual(self.client.get('/api/health').get_json(),{'status':'ok','transport':'web'})
    def test_private_api_requires_google_session(self): self.assertEqual(self.client.get('/api/status').status_code,401);self.assertEqual(self.client.post('/api/chat',json={'message':'test'}).status_code,401)
    def test_google_login_is_public(self):
        with patch('web_app.build_web_signin_url',return_value='https://accounts.google.test/auth') as build:
            r=self.client.get('/api/google/login')
        self.assertEqual(r.status_code,200);self.assertEqual(r.get_json()['url'],'https://accounts.google.test/auth');build.assert_called_once_with()
    def test_two_google_accounts_have_independent_sessions(self):
        a=self.app.test_client();b=self.app.test_client();aid=self._google_session(a,'sub-a','a@example.test','Alice');bid=self._google_session(b,'sub-b','b@example.test','Bob')
        self.assertNotEqual(aid,bid);self.assertEqual(a.get('/api/status').get_json()['user']['email'],'a@example.test');self.assertEqual(b.get('/api/status').get_json()['user']['email'],'b@example.test')
    def test_settings_are_isolated_between_google_users(self):
        a=self.app.test_client();b=self.app.test_client();self._google_session(a,'settings-a','sa@example.test','A');self._google_session(b,'settings-b','sb@example.test','B')
        a.post('/api/settings',json={'timezone':'Europe/Tallinn','buffer_minutes':45});b.post('/api/settings',json={'timezone':'Europe/Moscow','buffer_minutes':5})
        sa=a.get('/api/status').get_json();sb=b.get('/api/status').get_json();self.assertEqual(sa['timezone'],'Europe/Tallinn');self.assertEqual(sb['timezone'],'Europe/Moscow');self.assertEqual(sa['preferences']['buffer_minutes'],45);self.assertEqual(sb['preferences']['buffer_minutes'],5)
    def test_chat_routes_with_google_session_user_id(self):
        a=self.app.test_client();b=self.app.test_client();aid=self._google_session(a,'chat-a','ca@example.test','Alice');bid=self._google_session(b,'chat-b','cb@example.test','Bob');seen=[]
        async def fake_route(update,context,text=None):seen.append((update.effective_user.id,update.effective_user.full_name,text));await update.message.reply_text('ok');return True
        with patch('web_app.route_text',side_effect=fake_route):a.post('/api/chat',json={'message':'A'});b.post('/api/chat',json={'message':'B'})
        self.assertEqual(seen,[(aid,'Alice','A'),(bid,'Bob','B')])
    def test_logout_removes_google_session(self):
        self._google_session(self.client,'logout-sub','logout@example.test','Logout');self.assertEqual(self.client.get('/api/status').status_code,200);self.client.post('/api/logout');self.assertEqual(self.client.get('/api/status').status_code,401)
    def test_pwa_shell_and_manifest_are_served(self):
        r=self.client.get('/');self.assertEqual(r.status_code,200);r.close();m=self.client.get('/manifest.webmanifest');self.assertEqual(m.status_code,200);m.close()
if __name__=='__main__':unittest.main()
