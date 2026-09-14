import asyncio
from datetime import datetime, timedelta, timezone
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid
from zoneinfo import ZoneInfo

import web_app
from core import db
from core.ai_memory_store import list_ai_memory_events
from core.assistant_preferences import get_assistant_preferences, save_assistant_preferences, quiet_until, review_history
from core.command_store import enter_request, leave_request
from core.library_store import get_saved_reminder
from core.note_store import create_note, append_note, delete_note, get_note, list_notes
from core.notification_policy import save_policy, get_policy, activate_repeats, claim_repeat_attempts, postpone_transport
from core.push_store import save_push_subscription
from core.reminder_recurrence import next_repeat_after
from core.reminder_store import create_reminder, claim_due_for_push, complete_push_delivery, complete_reminder
from core.undo_store import last_note_action, undo_note_action
from modules.account_privacy import create_erase_challenge, erase_account, export_account, ERASE_CONFIRMATION, USER_TABLES
from modules import calendar_actions, calendar_availability, calendar_user, daily_review, reminder_dispatcher
from modules.command_templates import save_template, list_templates, delete_template, handle_template
from tests.web_test_support import web_test_app, request_id, push_keys


class FeatureCase(unittest.TestCase):
    def setUp(self):
        self.app = web_test_app()
        self.client = self.app.test_client()
        value = uuid.uuid4().hex
        self.user = db.get_or_create_google_user(value, value + "@example.test", "Test")
        db.save_user_timezone(self.user, "Europe/Moscow")
        with self.client.session_transaction() as stored:
            stored["user_id"] = self.user
            stored["auth_time"] = time.time()
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=self.user),
                                      message=SimpleNamespace(reply_text=AsyncMock()))
        self.context = SimpleNamespace(user_data={})

    def future_slot(self):
        zone = ZoneInfo("Europe/Moscow")
        start = datetime.now(zone).replace(hour=11, minute=0, second=0, microsecond=0) + timedelta(days=1)
        while start.weekday() >= 5:
            start += timedelta(days=1)
        return start, start + timedelta(minutes=40)


class QuietFallbackTests(FeatureCase):
    def test_empty_calendar_review_still_shows_available_windows(self):
        from zoneinfo import ZoneInfo
        now = datetime(2026, 9, 14, 8, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        with patch.object(daily_review, "_list_events", return_value=[]):
            report = daily_review.build_day_review(self.user, now=now)
        self.assertIn("Окна по 30 минут", report["text"])
        self.assertTrue(report["calendar_ok"])

    def test_quiet_hours_suppress_foreground_polling(self):
        create_reminder(self.user, "Quiet fallback", datetime.now(timezone.utc) - timedelta(minutes=1))
        with patch.object(web_app, "quiet_until", return_value=datetime.now(timezone.utc) + timedelta(hours=1)):
            response = self.client.get("/api/reminders/due")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reminders"], [])

    def test_quiet_hours_do_not_append_automatic_reminders_to_chat(self):
        with patch.object(web_app, "quiet_until", return_value=datetime.now(timezone.utc) + timedelta(hours=1)), patch.object(web_app, "claim_due_for_user") as claim:
            self.assertEqual(web_app._with_due_reminders(self.user, ["reply"]), ["reply"])
            claim.assert_not_called()


class FreeSlotFeatureTests(FeatureCase):
    def test_duration_question_uses_minutes_without_na(self):
        self.assertEqual(calendar_availability._requested_duration("когда завтра есть 40 минут"), timedelta(minutes=40))

    def test_time_is_checked_again_before_insert(self):
        start, end = self.future_slot()
        busy = {"start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
        with patch.object(calendar_availability, "_list_events", return_value=[busy]), patch.object(calendar_availability, "_create_event") as create:
            with self.assertRaises(ValueError):
                calendar_availability.create_event_in_slot(self.user, "Europe/Moscow", "Meeting", start, end)
        create.assert_not_called()

    def test_preferences_are_checked_again_before_insert(self):
        start, end = self.future_slot()
        db.save_calendar_preferences(self.user, work_start="12:00")
        with patch.object(calendar_availability, "_create_event") as create:
            with self.assertRaises(ValueError):
                calendar_availability.create_event_in_slot(self.user, "Europe/Moscow", "Meeting", start, end)
        create.assert_not_called()

    def test_free_slot_title_does_not_send_invitations(self):
        start, end = self.future_slot()
        with patch.object(calendar_availability, "_list_events", return_value=[]), patch.object(calendar_availability, "_create_event") as create:
            event = calendar_availability.create_event_in_slot(self.user, "Europe/Moscow", "Meeting guest@example.test", start, end)
        create.assert_called_once()
        self.assertNotIn("attendees", event)

    def test_selection_requires_confirmation(self):
        start, end = self.future_slot()
        pending = {"type": "free_slot_choice", "slots": [(start, end)], "timezone": "Europe/Moscow", "expires_at": time.time() + 300}
        self.context.user_data["smart_planner_pending"] = pending
        with patch.object(calendar_actions, "create_event_in_slot") as create:
            asyncio.run(calendar_actions.resume_pending_action(self.update, self.context, "1", pending))
            pending = self.context.user_data["smart_planner_pending"]
            asyncio.run(calendar_actions.resume_pending_action(self.update, self.context, "Meeting", pending))
            self.assertEqual(self.context.user_data["smart_planner_pending"]["type"], "confirm_free_slot")
        create.assert_not_called()

    def test_expired_offer_cannot_be_booked(self):
        start, end = self.future_slot()
        pending = {"type": "confirm_free_slot", "slot": (start, end), "title": "Meeting",
                   "timezone": "Europe/Moscow", "expires_at": time.time() - 1}
        self.context.user_data["smart_planner_pending"] = pending
        with patch.object(calendar_actions, "create_event_in_slot") as create:
            asyncio.run(calendar_actions.resume_pending_action(self.update, self.context, "да", pending))
        create.assert_not_called()

    def test_stale_button_does_not_select_a_different_dialog(self):
        with patch.object(web_app, "route_text") as route:
            response = self.client.post("/api/chat", json={"message": "1", "expected_context": "old-dialog"})
        self.assertEqual(response.status_code, 410)
        route.assert_not_called()

    def test_calendar_listing_reads_all_pages(self):
        service = Mock()
        service.events.return_value.list.return_value.execute.side_effect = [
            {"items": [{"id": "first"}], "nextPageToken": "second"},
            {"items": [{"id": "last"}]},
        ]
        start, end = self.future_slot()
        with patch.object(calendar_user, "_get_calendar_service", return_value=service):
            values = calendar_user._list_events(self.user, start, end)
        self.assertEqual([v["id"] for v in values], ["first", "last"])
        self.assertEqual(service.events.return_value.list.call_args.kwargs["pageToken"], "second")


class UndoFeatureTests(FeatureCase):
    def capture(self, callback):
        tokens = enter_request(self.user, request_id())
        try:
            return callback()
        finally:
            leave_request(tokens)

    def test_note_deletion_can_be_undone_once(self):
        note = create_note(self.user, "Keep me")
        self.capture(lambda: delete_note(self.user, note["note_id"]))
        action = last_note_action(self.user)
        self.assertIsNone(get_note(self.user, note["note_id"]))
        result = self.client.post(f"/api/assistant/undo/{action['id']}")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(get_note(self.user, note["note_id"])["text"], "Keep me")
        self.assertTrue(undo_note_action(self.user, action["id"])["already_undone"])

    def test_newer_edit_is_never_overwritten(self):
        note = create_note(self.user, "First")
        self.capture(lambda: append_note(self.user, note["note_id"], "Second"))
        action = last_note_action(self.user)
        append_note(self.user, note["note_id"], "Third")
        with self.assertRaises(ValueError):
            undo_note_action(self.user, action["id"])
        self.assertIn("Third", get_note(self.user, note["note_id"])["text"])

    def test_another_user_cannot_undo_my_action(self):
        self.capture(lambda: create_note(self.user, "Mine"))
        action = last_note_action(self.user)
        with self.assertRaises(ValueError):
            undo_note_action(self.user + 999999, action["id"])

    def test_journal_and_note_share_rollback(self):
        with patch("core.note_store.record_ai_memory_event", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(RuntimeError):
                self.capture(lambda: create_note(self.user, "Never saved"))
        self.assertEqual(list_notes(self.user), [])
        self.assertIsNone(last_note_action(self.user))

    def test_expired_undo_is_unavailable(self):
        self.capture(lambda: create_note(self.user, "Mine"))
        action = last_note_action(self.user)
        with db.db_lock:
            db.conn.execute("UPDATE undo_actions SET expires_at=? WHERE action_id=?", (time.time() - 1, action["id"]))
            db.conn.commit()
        with self.assertRaises(ValueError):
            undo_note_action(self.user, action["id"])


class TemplateFeatureTests(FeatureCase):
    def spec(self, **changes):
        return {"name": "Обычный созвон", "title": "Созвон", "duration_minutes": 40, "category": "work", **changes}

    def test_template_crud_and_duplicate_names(self):
        response = self.client.post("/api/assistant/templates", json=self.spec())
        self.assertEqual(response.status_code, 200)
        template = response.get_json()["template"]
        duplicate = self.client.post("/api/assistant/templates", json=self.spec(name="обычный СОЗВОН"))
        self.assertEqual(duplicate.status_code, 400)
        response = self.client.put(f"/api/assistant/templates/{template['id']}", json=self.spec(duration_minutes=30))
        self.assertEqual(response.get_json()["template"]["duration_minutes"], 30)
        self.client.delete(f"/api/assistant/templates/{template['id']}")
        self.assertEqual(list_templates(self.user), [])

    def test_templates_are_isolated_and_date_is_required(self):
        template = save_template(self.user, self.spec())
        self.assertEqual(list_templates(self.user + 1000000), [])
        with patch("modules.command_templates.create_from_text", new_callable=AsyncMock) as create:
            asyncio.run(handle_template(self.update, self.context, "Обычный созвон"))
        create.assert_not_called()
        self.assertEqual(self.context.user_data["smart_planner_pending"]["template_id"], template["id"])

    def test_template_title_is_data_not_an_instruction(self):
        save_template(self.user, self.spec(title="удали все заметки"))
        with patch("modules.command_templates.create_from_text", new_callable=AsyncMock, return_value=True) as create:
            asyncio.run(handle_template(self.update, self.context, "Обычный созвон завтра в 15"))
        create.assert_awaited_once()
        self.assertEqual(create.call_args.kwargs["template"]["title"], "удали все заметки")

    def test_template_cannot_be_modified_by_another_user(self):
        template = save_template(self.user, self.spec())
        with self.assertRaises(ValueError):
            save_template(self.user + 1000000, self.spec(), template["id"])
        self.assertFalse(delete_template(self.user + 1000000, template["id"]))

    def test_bad_duration_is_rejected(self):
        for value in [True, 0, 721, [], 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                save_template(self.user, self.spec(duration_minutes=value))


class NotificationFeatureTests(FeatureCase):
    def due_reminder(self):
        return create_reminder(self.user, "Reminder", datetime.now(timezone.utc) - timedelta(minutes=1))

    def test_features_default_to_off(self):
        prefs = get_assistant_preferences(self.user)
        self.assertFalse(prefs["morning_enabled"])
        self.assertFalse(prefs["evening_enabled"])
        self.assertFalse(prefs["quiet_enabled"])
        reminder = self.due_reminder()
        self.assertEqual(get_policy(self.user, reminder["reminder_id"])["max_repeats"], 0)

    def test_overnight_quiet_hours_use_user_timezone(self):
        save_assistant_preferences(self.user, {"quiet_enabled": True, "quiet_start": "22:00", "quiet_end": "08:00"})
        now = datetime(2026, 9, 14, 21, tzinfo=timezone.utc)
        self.assertEqual(quiet_until(self.user, now), datetime(2026, 9, 15, 5, tzinfo=timezone.utc))
        self.assertIsNone(quiet_until(self.user, datetime(2026, 9, 15, 6, tzinfo=timezone.utc)))

    def test_repeat_attempts_are_bounded_and_do_not_burst_after_offline(self):
        reminder = self.due_reminder()
        reminder_id = reminder["reminder_id"]
        save_policy(self.user, reminder_id, {"interval_minutes": 5, "max_repeats": 2})
        claim_due_for_push([self.user])
        complete_push_delivery(reminder_id)
        now = datetime.now(timezone.utc)
        activate_repeats(reminder, now)
        later = now + timedelta(hours=1)
        self.assertEqual(len(claim_repeat_attempts(self.user, later)), 1)
        self.assertEqual(claim_repeat_attempts(self.user, later), [])
        self.assertEqual(len(claim_repeat_attempts(self.user, later + timedelta(minutes=6))), 1)
        self.assertEqual(claim_repeat_attempts(self.user, later + timedelta(hours=1)), [])

    def test_completed_reminder_stops_notification_repeats(self):
        reminder = self.due_reminder()
        save_policy(self.user, reminder["reminder_id"], {"interval_minutes": 5, "max_repeats": 5})
        claim_due_for_push([self.user])
        complete_push_delivery(reminder["reminder_id"])
        now = datetime.now(timezone.utc)
        activate_repeats(reminder, now)
        complete_reminder(self.user, reminder["reminder_id"])
        self.assertEqual(claim_repeat_attempts(self.user, now + timedelta(hours=1)), [])

    def test_failed_transport_waits_before_new_attempt(self):
        reminder = self.due_reminder()
        claimed = claim_due_for_push([self.user])[0]
        postpone_transport(claimed, datetime.now(timezone.utc))
        from core.reminder_store import release_push_delivery
        release_push_delivery(reminder["reminder_id"], "temporary")
        self.assertEqual(claim_due_for_push([self.user]), [])

    def test_recurrence_skips_missed_days_without_changing_clock_time(self):
        zone = ZoneInfo("Europe/Moscow")
        original = datetime(2026, 9, 1, 20, tzinfo=zone)
        now = datetime(2026, 9, 14, 21, tzinfo=zone)
        self.assertEqual(next_repeat_after(original, "daily", str(zone), now).astimezone(zone), datetime(2026, 9, 15, 20, tzinfo=zone))
        weekly = next_repeat_after(original, "weekly", str(zone), now).astimezone(zone)
        self.assertEqual((weekly.weekday(), weekly.hour), (original.weekday(), 20))
        self.assertGreater(weekly, now)

    def test_partial_success_does_not_resend_to_successful_devices(self):
        reminder = self.due_reminder()
        with patch.object(reminder_dispatcher, "list_push_user_ids", return_value=[self.user]), \
             patch.object(reminder_dispatcher, "_deliver_payload", return_value={"accepted": 1, "failed": 1, "removed": 0, "subscriptions": 2, "errors": ["Timeout"]}) as deliver:
            first = reminder_dispatcher.dispatch_due_reminders_once()
            second = reminder_dispatcher.dispatch_due_reminders_once()
        self.assertEqual(first["delivered"], 1)
        self.assertEqual(second["delivered"], 0)
        self.assertEqual(deliver.call_count, 1)
        self.assertEqual(get_saved_reminder(self.user, reminder["reminder_id"])["status"], "delivered")

    def test_quiet_worker_never_claims_or_sends(self):
        with patch.object(reminder_dispatcher, "list_push_user_ids", return_value=[self.user]), \
             patch.object(reminder_dispatcher, "quiet_until", return_value=datetime.now(timezone.utc) + timedelta(hours=1)), \
             patch.object(reminder_dispatcher, "claim_due_for_push") as claim, \
             patch.object(reminder_dispatcher, "_deliver_payload") as deliver:
            reminder_dispatcher.dispatch_due_reminders_once()
        claim.assert_not_called()
        deliver.assert_not_called()

    def test_foreign_reminder_policy_is_rejected(self):
        reminder = self.due_reminder()
        with self.assertRaises(ValueError):
            save_policy(self.user + 1000000, reminder["reminder_id"], {"interval_minutes": 5, "max_repeats": 2})

    def test_push_subscription_cannot_be_transferred_between_accounts(self):
        endpoint = "https://fcm.googleapis.com/fcm/send/" + uuid.uuid4().hex
        keys = push_keys()
        save_push_subscription(self.user, endpoint, **keys)
        with self.assertRaises(ValueError):
            save_push_subscription(self.user + 1000000, endpoint, **keys)


class ReviewFeatureTests(FeatureCase):
    def test_calendar_failure_is_not_reported_as_empty_day(self):
        with patch.object(daily_review, "_list_events", side_effect=RuntimeError("unavailable")):
            review = daily_review.build_day_review(self.user)
        self.assertFalse(review["calendar_ok"])
        self.assertIn("не проверена", review["text"])
        self.assertNotIn("встреч нет", review["text"])

    def test_review_delivered_only_once_after_restart(self):
        save_assistant_preferences(self.user, {"morning_enabled": True})
        now = datetime(2026, 9, 14, 5, 10, tzinfo=timezone.utc)
        sender = Mock(return_value=True)
        with patch.object(daily_review, "build_day_review", return_value={"text": "Review"}):
            self.assertEqual(daily_review.deliver_reviews_for_user(self.user, sender, now=now), 1)
            self.assertEqual(daily_review.deliver_reviews_for_user(self.user, sender, now=now), 0)
        sender.assert_called_once()
        self.assertEqual(review_history(self.user)[0]["phase"], "accepted")

    def test_missed_review_is_recorded_without_late_push(self):
        save_assistant_preferences(self.user, {"morning_enabled": True})
        sender = Mock()
        daily_review.deliver_reviews_for_user(self.user, sender, now=datetime(2026, 9, 14, 11, tzinfo=timezone.utc))
        sender.assert_not_called()
        self.assertEqual(review_history(self.user)[0]["phase"], "missed")

    def test_evening_review_does_not_change_any_reminders(self):
        reminder = create_reminder(self.user, "Leave alone", datetime.now(timezone.utc) - timedelta(minutes=1))
        with patch.object(daily_review, "_list_events", return_value=[]):
            review = daily_review.build_day_review(self.user, "evening")
        self.assertEqual(review["reminders"][0]["id"], reminder["reminder_id"])
        self.assertEqual(get_saved_reminder(self.user, reminder["reminder_id"])["status"], "pending")


class PrivacyFeatureTests(FeatureCase):
    def test_export_has_my_data_without_tokens_or_other_users(self):
        db.save_google_token(self.user, {"access_token": "super-secret-access", "refresh_token": "super-secret-refresh"})
        create_note(self.user, "My content")
        create_note(self.user + 1000000, "Other person's content")
        response = self.client.get("/api/privacy/export")
        self.assertEqual(response.status_code, 200)
        content = response.get_data(as_text=True)
        self.assertIn("My content", content)
        self.assertNotIn("Other person's content", content)
        self.assertNotIn("super-secret", content)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_local_erasure_requires_recent_login_and_explicit_confirmation(self):
        with self.client.session_transaction() as stored:
            stored["auth_time"] = time.time() - 601
        response = self.client.post("/api/privacy/challenge")
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(db.get_google_account(self.user))

    def test_local_erasure_drops_live_rows_and_invalidates_old_sessions(self):
        note = create_note(self.user, "Erase me")
        reminder = create_reminder(self.user, "Erase reminder", datetime.now(timezone.utc) + timedelta(hours=1))
        save_template(self.user, {"name": "My meeting", "title": "Meeting"})
        save_assistant_preferences(self.user, {"morning_enabled": True})
        old = self.app.test_client()
        with old.session_transaction() as stored:
            stored["user_id"] = self.user
        token = self.client.post("/api/privacy/challenge").get_json()["challenge"]
        with patch("modules.calendar._create_event") as calendar:
            result = self.client.post("/api/privacy/erase", json={"challenge": token, "confirmation": ERASE_CONFIRMATION})
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(db.get_google_account(self.user))
        self.assertEqual(old.get("/api/status").status_code, 401)
        calendar.assert_not_called()
        with db.db_lock:
            existing = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in USER_TABLES:
                if table in existing:
                    self.assertEqual(db.conn.execute(f"SELECT count(*) FROM {table} WHERE user_id=?", (self.user,)).fetchone()[0], 0, table)

    def test_bad_confirmation_preserves_data(self):
        create_note(self.user, "Keep me")
        challenge = create_erase_challenge(self.user)
        with self.assertRaises(ValueError):
            erase_account(self.user, challenge, "not confirmed")
        self.assertEqual(len(list_notes(self.user)), 1)

    def test_other_user_cannot_use_erasure_challenge(self):
        token = create_erase_challenge(self.user)
        with self.assertRaises(ValueError):
            erase_account(self.user + 1000000, token, ERASE_CONFIRMATION)
        self.assertIsNotNone(db.get_google_account(self.user))

    def test_deleted_account_cannot_receive_new_push(self):
        token = create_erase_challenge(self.user)
        erase_account(self.user, token, ERASE_CONFIRMATION)
        with patch.object(reminder_dispatcher, "list_push_user_ids", return_value=[self.user]), \
             patch.object(reminder_dispatcher, "claim_due_for_push") as claim, \
             patch.object(reminder_dispatcher, "_deliver_payload") as deliver:
            reminder_dispatcher.dispatch_due_reminders_once()
        claim.assert_not_called()
        deliver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
