import unittest
from unittest.mock import MagicMock,patch
import web_app
from core.db import get_or_create_google_user
class WebCalendarE2ETests(unittest.TestCase):
    def setUp(self):
        self.app=web_app.create_web_app();self.client=self.app.test_client();web_app._user_state.clear();self.user_id=get_or_create_google_user('e2e-google-sub','e2e@example.test','E2E User')
        with self.client.session_transaction() as s:s['user_id']=self.user_id
    def _set_timezone(self):self.assertEqual(self.client.post('/api/settings',json={'timezone':'Europe/Moscow'}).status_code,200)
    def _service(self):
        service=MagicMock();service.events.return_value.insert.return_value.execute.return_value={'id':'event-1'};return service
    def test_web_api_creates_calendar_event_through_google_insert(self):
        self._set_timezone();service=self._service()
        with patch('modules.calendar_actions._find_conflicts',return_value=[]),patch('modules.calendar.get_google_token',return_value={'token':'test'}),patch('modules.calendar.Credentials.from_authorized_user_info',return_value=MagicMock()),patch('modules.calendar.build',return_value=service):r=self.client.post('/api/chat',json={'message':'поставь врача 20.09.2030 в 19:00 на час'})
        self.assertEqual(r.status_code,200);self.assertTrue(any('добавлено' in x.lower() for x in r.get_json()['replies']));service.events.return_value.insert.assert_called_once();event=service.events.return_value.insert.call_args.kwargs['body'];self.assertEqual(event['start']['timeZone'],'Europe/Moscow');self.assertEqual(event['start']['dateTime'][:16],'2030-09-20T19:00');self.assertEqual(event['end']['dateTime'][:16],'2030-09-20T20:00')
    def test_web_two_step_create_keeps_pending_state_between_requests(self):
        self._set_timezone();service=self._service();first=self.client.post('/api/chat',json={'message':'поставь врача 20.09.2030'});self.assertEqual(first.get_json()['replies'],['Во сколько поставить событие?'])
        with patch('modules.calendar_actions._find_conflicts',return_value=[]),patch('modules.calendar.get_google_token',return_value={'token':'test'}),patch('modules.calendar.Credentials.from_authorized_user_info',return_value=MagicMock()),patch('modules.calendar.build',return_value=service):second=self.client.post('/api/chat',json={'message':'19:00'})
        self.assertEqual(second.status_code,200);self.assertTrue(any('добавлено' in x.lower() for x in second.get_json()['replies']));service.events.return_value.insert.assert_called_once()
    def test_two_users_calendar_calls_keep_distinct_user_ids(self):
        a=self.app.test_client();b=self.app.test_client();aid=get_or_create_google_user('calendar-a','calendar-a@example.test','A');bid=get_or_create_google_user('calendar-b','calendar-b@example.test','B')
        with a.session_transaction() as s:s['user_id']=aid
        with b.session_transaction() as s:s['user_id']=bid
        seen=[]
        async def fake_route(update,context,text=None):seen.append(update.effective_user.id);await update.message.reply_text('ok');return True
        with patch('web_app.route_text',side_effect=fake_route):a.post('/api/chat',json={'message':'event A'});b.post('/api/chat',json={'message':'event B'})
        self.assertEqual(seen,[aid,bid]);self.assertNotEqual(aid,bid)
if __name__=='__main__':unittest.main()
