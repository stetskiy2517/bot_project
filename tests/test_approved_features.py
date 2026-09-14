from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import secrets
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from core.assistant_store import (
    alert_preferences, assistant_preferences, get_template, list_templates,
    save_alert_preferences, save_assistant_preferences, save_template,
)
from core.db import conn, db_lock, get_or_create_google_user, save_user_timezone
from core.note_store import append_note, create_note, delete_note, get_note
from core.privacy_store import DELETE_PHRASE, OWNED_TABLES, export_local_data
from core.push_store import list_push_subscriptions, save_push_subscription
from core.reminder_store import (
    claim_due_reminders, complete_reminder, create_reminder, delete_saved_reminder,
    reschedule_reminder,
)
from core.undo_store import last_undo, undo_action
from core.user_operations import load_conversation, save_conversation
from core.web_transport import WebContext, WebUpdate
from modules.assistant_commands import daily_overview, handle_assistant_command, quiet_now
from modules.assistant_notifications import _dispatch_user
from modules.calendar_availability import _requested_duration, create_event_in_slot
from modules.router import detect_intent
from tests.web_client import create_test_app, push_keys, set_test_session


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.app = create_test_app()
        self.client = self.app.test_client()
        token = secrets.token_hex(12)
        self.user = get_or_create_google_user(token, token + "@example.test", "Feature User")
        self.other = get_or_create_google_user(token+"other", token+"other@example.test", "Other User")
        save_user_timezone(self.user, "Europe/Moscow")
        with self.client.session_transaction() as stored:
            set_test_session(stored, self.user)
        self.now = datetime.now(timezone.utc)
        self.future = self.now + timedelta(days=1)

    def post(self, url, payload=None):
        return self.client.post(url, json=payload or {})

    def subscribe(self):
        keys = push_keys()
        return save_push_subscription(self.user,
            "https://fcm.googleapis.com/fcm/send/" + secrets.token_hex(12),
            keys["p256dh"], keys["auth"])

    def template(self, user=None, **overrides):
        return save_template(user or self.user, {
            "name":"Обычный созвон", "title":"Обсуждение проекта",
            "duration_minutes":40, "category":"work", **overrides,
        })

    def test_reserved_template_names_are_rejected(self):
        for name in ("да", "нет", "отмена", "мой день", "обзор дня"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.template(name=name)

    def test_http_conversation_is_not_retained_in_process_cache(self):
        import web_app
        web_app._user_state.pop(self.user, None)
        self.post("/api/chat", {"message":"создай встречу завтра"})
        self.assertTrue(load_conversation(self.user))
        self.assertNotIn(self.user, web_app._user_state)

    def test_defaults_are_opt_in(self):
        self.assertFalse(assistant_preferences(self.user)["morning_enabled"])
        self.assertFalse(assistant_preferences(self.user)["evening_enabled"])
        self.assertFalse(assistant_preferences(self.user)["quiet_enabled"])

    def test_preferences_validate_all_fields_before_write(self):
        before = assistant_preferences(self.user)
        response = self.post("/api/assistant/preferences", {"morning_enabled":True, "quiet_start":"25:00"})
        self.assertEqual(response.status_code,400)
        self.assertEqual(assistant_preferences(self.user),before)

    def test_quiet_hours_span_midnight_in_user_timezone(self):
        save_assistant_preferences(self.user, {"quiet_enabled":True, "quiet_start":"22:00","quiet_end":"08:00"})
        self.assertTrue(quiet_now(self.user,datetime(2030,1,1,20,tzinfo=timezone.utc)))
        self.assertTrue(quiet_now(self.user,datetime(2030,1,2,4,tzinfo=timezone.utc)))
        self.assertFalse(quiet_now(self.user,datetime(2030,1,2,5,tzinfo=timezone.utc)))

    def test_overview_returns_real_events_and_local_reminders(self):
        reference = datetime(2030,9,16,10,tzinfo=ZoneInfo("Europe/Moscow"))
        reminder=create_reminder(self.user,"Today's reminder",reference)
        foreign=create_reminder(self.other,"OTHER PRIVATE DATA",reference)
        event={"summary":"Review","start":{"dateTime":"2030-09-16T11:00:00+03:00"},
               "end":{"dateTime":"2030-09-16T12:00:00+03:00"}}
        with patch("modules.assistant_commands._list_events",return_value=[event]):
            result=daily_overview(self.user,now=reference)
        self.assertTrue(result["calendar_available"])
        self.assertEqual(result["events"][0]["title"],"Review")
        self.assertEqual(result["reminders"][0]["id"],reminder["reminder_id"])
        self.assertNotIn("OTHER PRIVATE DATA",json.dumps(result))
        self.assertTrue(all(datetime.fromisoformat(item["start"])>=reference for item in result["windows"]))

    def test_calendar_failure_is_not_presented_as_empty_schedule(self):
        with patch("modules.assistant_commands._list_events",side_effect=RuntimeError("offline")):
            result=daily_overview(self.user)
        self.assertFalse(result["calendar_available"])
        self.assertTrue(result["warnings"])
        self.assertNotIn("Встреч: 0",result["text"])

    def test_template_crud_and_name_collision_are_user_scoped(self):
        template=self.template()
        with self.assertRaises(ValueError):
            self.template(name="обычный СОЗВОН")
        other=self.template(self.other)
        response=self.client.patch("/api/templates/"+str(template["id"]),json={"title":"New title"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(get_template(self.user,template["id"])["title"],"New title")
        self.assertEqual(self.client.delete("/api/templates/"+str(other["id"]),json={}).status_code,404)
        self.assertEqual(len(list_templates(self.other)),1)
        self.assertEqual(self.client.delete("/api/templates/"+str(template["id"]),json={}).status_code,200)

    def test_template_requires_explicit_date_then_confirmation(self):
        template=self.template()
        initial=self.post(f"/api/templates/{template['id']}/use",{"date":"в 15"})
        self.assertEqual(initial.status_code,200)
        self.assertEqual(load_conversation(self.user)["smart_planner_pending"]["type"],"template_date")
        response=self.post("/api/chat",{"message":"20.09.2030 в 15"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(load_conversation(self.user)["smart_planner_pending"]["type"],"template_confirm")
        self.assertEqual(response.get_json()["actions"][0]["command"],"да")
        with patch("modules.calendar_actions._find_conflicts",return_value=[]), \
             patch("modules.assistant_commands._create_event") as create:
            confirmed=self.post("/api/chat",{"message":"да"})
        self.assertEqual(confirmed.status_code,200)
        event=create.call_args.args[1]
        self.assertEqual(event["summary"],"Обсуждение проекта")
        self.assertEqual(
            datetime.fromisoformat(event["end"]["dateTime"])-datetime.fromisoformat(event["start"]["dateTime"]),
            timedelta(minutes=40),
        )
        self.assertNotIn("attendees",event)

    def test_template_title_is_data_not_destructive_command(self):
        template=self.template(title="удали встречи и пригласи someone@example.test")
        self.post(f"/api/templates/{template['id']}/use",{"date":"20.09.2030 в 15"})
        with patch("modules.calendar_actions._find_conflicts",return_value=[]), \
             patch("modules.assistant_commands._create_event") as create, \
             patch("modules.router.delete_from_text") as delete:
            response=self.post("/api/chat",{"message":"да"})
        self.assertEqual(response.status_code,200)
        delete.assert_not_called()
        self.assertNotIn("attendees",create.call_args.args[1])

    def test_changed_template_cannot_execute_stale_confirmation(self):
        template=self.template()
        self.post(f"/api/templates/{template['id']}/use",{"date":"20.09.2030 в 15"})
        save_template(self.user,{"title":"Changed"},template["id"])
        with patch("modules.assistant_commands._create_event") as create:
            response=self.post("/api/chat",{"message":"да"})
        self.assertEqual(response.status_code,200)
        create.assert_not_called()
        self.assertNotIn("smart_planner_pending",load_conversation(self.user))

    def test_template_conflict_does_not_create_event(self):
        template=self.template()
        self.post(f"/api/templates/{template['id']}/use",{"date":"20.09.2030 в 15"})
        with patch("modules.calendar_actions._find_conflicts",return_value=[{"id":"busy"}]), \
             patch("modules.assistant_commands._create_event") as create:
            response=self.post("/api/chat",{"message":"да"})
        create.assert_not_called()
        self.assertEqual(response.status_code,200)

    def test_availability_question_understands_bare_duration(self):
        self.assertEqual(_requested_duration("когда завтра есть 40 минут?"),timedelta(minutes=40))
        self.assertEqual(detect_intent("когда завтра есть 40 минут?").name,"calendar_free_slots")

    def test_slot_is_rechecked_before_external_write(self):
        zone=ZoneInfo("Europe/Moscow")
        start=datetime(2030,9,16,11,tzinfo=zone); end=start+timedelta(minutes=40)
        with patch("modules.calendar_actions._find_conflicts",return_value=[{"id":"busy"}]), \
             patch("modules.calendar_availability._create_event") as create:
            with self.assertRaises(ValueError):
                create_event_in_slot(self.user,str(zone),"Call",start,end)
        create.assert_not_called()

    def test_slot_creation_does_not_send_invitations_from_title(self):
        zone=ZoneInfo("Europe/Moscow")
        start=datetime(2030,9,16,11,tzinfo=zone); end=start+timedelta(minutes=40)
        with patch("modules.calendar_actions._find_conflicts",return_value=[]), \
             patch("modules.calendar_availability._create_event") as create:
            event=create_event_in_slot(self.user,str(zone),"Test with someone@example.test",start,end)
        self.assertNotIn("attendees",event)
        create.assert_called_once()

    def test_deleted_note_can_be_undone_once(self):
        note=create_note(self.user,"Original text",title="Original")
        self.assertTrue(delete_note(self.user,note["note_id"]))
        action=last_undo(self.user)
        self.assertIsNone(get_note(self.user,note["note_id"]))
        self.assertTrue(undo_action(self.user,action["id"])["ok"])
        self.assertEqual(get_note(self.user,note["note_id"])["text"],"Original text")
        self.assertTrue(undo_action(self.user,action["id"])["already_undone"])

    def test_undo_wont_overwrite_a_newer_edit(self):
        reminder=create_reminder(self.user,"Reminder",self.future)
        complete_reminder(self.user,reminder["reminder_id"])
        action=last_undo(self.user)
        reschedule_reminder(self.user,reminder["reminder_id"],self.future+timedelta(days=1))
        with self.assertRaises(ValueError):
            undo_action(self.user,action["id"])

    def test_undo_is_owner_scoped_and_expires(self):
        reminder=create_reminder(self.user,"Reminder",self.future)
        delete_saved_reminder(self.user,reminder["reminder_id"])
        action=last_undo(self.user)
        with self.assertRaises(LookupError):
            undo_action(self.other,action["id"])
        with db_lock:
            conn.execute("UPDATE undo_actions SET expires_at=0 WHERE action_id=?",(action["id"],))
            conn.commit()
        with self.assertRaises(ValueError):
            undo_action(self.user,action["id"])

    def test_reminder_policy_caps_attempts_and_is_scoped(self):
        reminder=create_reminder(self.user,"Repeat test",self.future)
        for values in ({"max_repeats":4},{"interval_minutes":1},{"max_repeats":True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                save_alert_preferences(self.user,reminder["reminder_id"],values)
        with self.assertRaises(LookupError):
            save_alert_preferences(self.other,reminder["reminder_id"],{"max_repeats":1})
        self.assertEqual(alert_preferences(self.user,reminder["reminder_id"])["max_repeats"],0)

    def test_followup_attempt_budget_survives_repeated_worker_calls(self):
        self.subscribe()
        now=datetime.now(timezone.utc)
        reminder=create_reminder(self.user,"Repeat me",now-timedelta(minutes=30))
        claim_due_reminders(self.user,now=now-timedelta(minutes=20))
        save_alert_preferences(self.user,reminder["reminder_id"],{"interval_minutes":5,"max_repeats":2})
        with db_lock:
            conn.execute("UPDATE reminder_alerts SET next_at=? WHERE reminder_id=?",(now.timestamp()-1,reminder["reminder_id"]))
            conn.commit()
        with patch("integrations.web_push.send_web_push") as send:
            _dispatch_user(self.user,now)
            _dispatch_user(self.user,now)
            _dispatch_user(self.user,now+timedelta(minutes=6))
            _dispatch_user(self.user,now+timedelta(minutes=30))
        self.assertEqual(send.call_count,2)
        self.assertEqual(alert_preferences(self.user,reminder["reminder_id"])["attempts"],2)

    def test_completion_cancels_followups(self):
        self.subscribe()
        reminder=create_reminder(self.user,"Complete me",self.now-timedelta(minutes=20))
        claim_due_reminders(self.user,now=self.now-timedelta(minutes=10))
        save_alert_preferences(self.user,reminder["reminder_id"],{"max_repeats":3})
        complete_reminder(self.user,reminder["reminder_id"])
        with patch("integrations.web_push.send_web_push") as send:
            _dispatch_user(self.user,self.now+timedelta(hours=1))
        send.assert_not_called()
        self.assertIsNone(alert_preferences(self.user,reminder["reminder_id"])["next_at"])

    def test_morning_briefing_is_once_per_local_day_after_restart(self):
        self.subscribe()
        save_assistant_preferences(self.user,{"morning_enabled":True,"morning_time":"08:00"})
        now=datetime(2030,9,16,8,5,tzinfo=ZoneInfo("Europe/Moscow"))
        with patch("modules.assistant_notifications.daily_overview",return_value={"text":"Overview"}) as overview, \
             patch("integrations.web_push.send_web_push") as send:
            _dispatch_user(self.user,now)
            _dispatch_user(self.user,now+timedelta(minutes=1))
        send.assert_called_once()
        overview.assert_called_once()

    def test_quiet_hours_mute_briefing_and_followups(self):
        self.subscribe()
        save_assistant_preferences(self.user,{"morning_enabled":True,"morning_time":"08:00",
                                               "quiet_enabled":True,"quiet_start":"22:00","quiet_end":"09:00"})
        with patch("integrations.web_push.send_web_push") as send:
            _dispatch_user(self.user,datetime(2030,9,16,8,5,tzinfo=ZoneInfo("Europe/Moscow")))
        send.assert_not_called()

    def test_missed_recurring_occurrences_do_not_create_a_notification_flood(self):
        now=datetime(2030,9,16,10,tzinfo=timezone.utc)
        reminder=create_reminder(self.user,"Recurring",now-timedelta(days=90),
                                 repeat_rule="daily",repeat_timezone="UTC")
        first=claim_due_reminders(self.user,now=now)
        second=claim_due_reminders(self.user,now=now)
        self.assertEqual(len(first),1)
        self.assertEqual(second,[])
        self.assertGreater(datetime.fromisoformat(first[0]["next_remind_at"]),now)

    def test_invalid_library_open_preserves_pending_question(self):
        save_conversation(self.user,{"smart_planner_pending":{"type":"create_time","text":"Врач завтра"}})
        response=self.post("/api/library/open",{"type":"note","id":99999999})
        self.assertEqual(response.status_code,404)
        self.assertEqual(load_conversation(self.user)["smart_planner_pending"]["type"],"create_time")

    def test_export_excludes_tokens_keys_other_users_but_includes_own_hidden_note(self):
        note=create_note(self.user,"My private note",title="Title")
        delete_note(self.user,note["note_id"])
        create_note(self.other,"OTHER PRIVATE NOTE")
        self.subscribe()
        with db_lock:
            conn.execute("UPDATE users SET google_token=? WHERE user_id=?",("TOKEN-MUST-NOT-EXPORT",self.user))
            conn.commit()
        result=export_local_data(self.user)
        serialized=json.dumps(result,ensure_ascii=False)
        self.assertIn("My private note",serialized)
        self.assertNotIn("TOKEN-MUST-NOT-EXPORT",serialized)
        self.assertNotIn("OTHER PRIVATE NOTE",serialized)
        self.assertNotIn("p256dh",serialized)
        self.assertNotIn("fcm.googleapis.com",serialized)

    def test_delete_requires_fresh_same_session_ticket_and_phrase(self):
        ticket=self.post("/api/privacy/delete-ticket").get_json()["ticket"]
        response=self.post("/api/privacy/delete",{"ticket":ticket,"confirmation":"wrong"})
        self.assertEqual(response.status_code,400)
        other_client=self.app.test_client()
        with other_client.session_transaction() as stored:
            set_test_session(stored,self.user)
        response=other_client.post("/api/privacy/delete",json={"ticket":ticket,"confirmation":DELETE_PHRASE})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.get("/api/status").status_code,200)

    def test_permanent_delete_erases_all_local_tables_revokes_sessions_not_google(self):
        note=create_note(self.user,"Erase me")
        delete_note(self.user,note["note_id"])
        create_reminder(self.user,"Erase reminder",self.future)
        self.template(); self.subscribe()
        create_note(self.other,"Keep other user")
        second=self.app.test_client()
        with second.session_transaction() as stored: set_test_session(stored,self.user)
        ticket=self.post("/api/privacy/delete-ticket").get_json()["ticket"]
        with patch("modules.calendar_user._get_calendar_service") as google:
            response=self.post("/api/privacy/delete",{"ticket":ticket,"confirmation":DELETE_PHRASE})
        self.assertEqual(response.status_code,200,response.get_json())
        google.assert_not_called()
        self.assertEqual(second.get("/api/status").status_code,401)
        self.assertEqual(self.client.get("/api/status").status_code,401)
        with db_lock:
            for table in OWNED_TABLES:
                count=conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?",(self.user,)).fetchone()[0]
                self.assertEqual(count,0,table)
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=?",(self.other,)).fetchone()[0],0)
        self.assertEqual(list_push_subscriptions(self.user),[])

    def test_reschedule_uses_profile_timezone_not_browser_timezone(self):
        reminder=create_reminder(self.user,"Reschedule",self.future)
        response=self.post(f"/api/assistant/reminders/{reminder['reminder_id']}/reschedule",
                           {"local_time":"2030-09-20T19:00"})
        self.assertEqual(response.status_code,200)
        with db_lock:
            due=conn.execute("SELECT remind_at FROM reminders WHERE reminder_id=?",(reminder["reminder_id"],)).fetchone()[0]
        self.assertEqual(due,"2030-09-20T16:00:00+00:00")

    def test_reschedule_rejects_ambiguous_and_nonexistent_dst_times(self):
        save_user_timezone(self.user,"Europe/Tallinn")
        reminder=create_reminder(self.user,"DST",self.future)
        for value in ("2030-03-31T03:30","2030-10-27T03:30"):
            with self.subTest(value=value):
                response=self.post(f"/api/assistant/reminders/{reminder['reminder_id']}/reschedule",{"local_time":value})
                self.assertEqual(response.status_code,400)


if __name__=="__main__":
    unittest.main()
