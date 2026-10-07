from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch, Mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from integrations import ai
from core.db import conn, db_lock, get_or_create_google_user
from core.memory_store import create_commitment, create_company, get_commitment, upsert_memory, search_work_memory
from core.task_planner_store import get_planner_task, list_planner_tasks
from core.reminder_store import complete_reminder
from core.erasure_journal import enforce_erasure_journal, record_erasure, journal_path, runtime_lock
from core.runtime_status import monitor_token, RUNNING_SHA
from scripts.backup_state import create_backup
from scripts.restore_state import restore_snapshot
from tests.web_test_support import web_test_app


class CertificateTrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = ai.AISettings(True, "gigachat", "model", "test-only", "scope", "https://api.example", "https://auth.example", 5, 100, None)

    def tearDown(self):
        self.temp.cleanup()

    def certificate(self, expired=False):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Russian Trusted Root CA")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(days=10)).not_valid_after(now+timedelta(days=-1 if expired else 30))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key,hashes.SHA256()))
        return cert.public_bytes(serialization.Encoding.PEM), cert.fingerprint(hashes.SHA256()).hex()

    def test_common_name_does_not_authenticate_arbitrary_root(self):
        content, _ = self.certificate()
        path = self.root / "root.pem"; path.write_bytes(content)
        with self.assertRaises(ai.AIProviderError):
            ai._validate_gigachat_root(path, "0"*64)
        with self.assertRaises(ai.AIConfigurationError):
            ai._validate_gigachat_root(path)

    def test_pin_and_expiration_are_both_required(self):
        content, pin = self.certificate(); path=self.root/"root.pem"; path.write_bytes(content)
        ai._validate_gigachat_root(path,pin)
        content, pin=self.certificate(expired=True);path.write_bytes(content)
        with self.assertRaises(ai.AIProviderError): ai._validate_gigachat_root(path,pin)

    def test_verified_download_and_cache_bundle_reconstruction(self):
        content,pin=self.certificate()
        root=self.root/"root.pem"; bundle=self.root/"bundle.pem"
        response=Mock(status_code=200,content=content)
        with patch.object(ai,"DEFAULT_GIGACHAT_ROOT_CERT",root), patch.object(ai,"DEFAULT_GIGACHAT_CA_BUNDLE",bundle), patch.object(ai.requests,"get",return_value=response) as download:
            ai._ensure_gigachat_ca_bundle(replace(self.settings,root_sha256=pin))
            self.assertIs(download.call_args.kwargs["verify"],True)
            bundle.write_bytes(b"old-unverified-bundle")
            ai._ensure_gigachat_ca_bundle(replace(self.settings,root_sha256=pin))
            self.assertIn(content,bundle.read_bytes())
            self.assertNotIn(b"old-unverified-bundle",bundle.read_bytes())
            self.assertEqual(download.call_count,1)

    def test_tls_failure_keeps_unverified_artifacts_out(self):
        _,pin=self.certificate()
        with patch.object(ai,"DEFAULT_GIGACHAT_ROOT_CERT",self.root/"root.pem"), patch.object(ai,"DEFAULT_GIGACHAT_CA_BUNDLE",self.root/"bundle.pem"), patch.object(ai.requests,"get",side_effect=ai.requests.exceptions.SSLError("test TLS failure")):
            with self.assertRaises(ai.AIProviderError): ai._ensure_gigachat_ca_bundle(replace(self.settings,root_sha256=pin))
        self.assertFalse((self.root/"bundle.pem").exists())

    def test_appended_unpinned_certificate_is_never_added_to_trust_bundle(self):
        content,pin=self.certificate();untrusted,_=self.certificate()
        root=self.root/"root.pem";bundle=self.root/"bundle.pem"
        with patch.object(ai,"DEFAULT_GIGACHAT_ROOT_CERT",root), patch.object(ai,"DEFAULT_GIGACHAT_CA_BUNDLE",bundle), patch.object(ai.requests,"get",return_value=Mock(status_code=200,content=content+untrusted)):
            ai._ensure_gigachat_ca_bundle(replace(self.settings,root_sha256=pin))
            self.assertEqual(root.read_bytes(),content)
            self.assertNotIn(untrusted,bundle.read_bytes())
            # Also repair a cached root containing an appended certificate.
            root.write_bytes(content+untrusted)
            ai._ensure_gigachat_ca_bundle(replace(self.settings,root_sha256=pin))
            self.assertNotIn(untrusted,bundle.read_bytes())

    def test_wrong_downloaded_pin_leaves_no_trust_artifacts(self):
        content,_=self.certificate()
        with patch.object(ai,"DEFAULT_GIGACHAT_ROOT_CERT",self.root/"root.pem"), patch.object(ai,"DEFAULT_GIGACHAT_CA_BUNDLE",self.root/"bundle.pem"), patch.object(ai.requests,"get",return_value=Mock(status_code=200,content=content)):
            with self.assertRaises(ai.AIProviderError):ai._ensure_gigachat_ca_bundle(replace(self.settings,root_sha256="0"*64))
        self.assertFalse((self.root/"root.pem").exists())
        self.assertFalse((self.root/"bundle.pem").exists())


class ProductReviewIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=web_test_app()

    def setUp(self):
        tag=uuid.uuid4().hex
        self.user=get_or_create_google_user(tag,tag+"@example.test","Review")
        self.other=get_or_create_google_user(tag+"other",tag+"other@example.test","Other")
        self.client=self.app.test_client()
        with self.client.session_transaction() as session: session["user_id"]=self.user

    def test_memory_search_covers_1001_rows_and_keeps_user_scope(self):
        now=datetime.now(timezone.utc).isoformat()
        with db_lock:
            conn.executemany("INSERT INTO sales_contacts(user_id,full_name,status,created_at,updated_at) VALUES (?,?,'active',?,?)",[(self.user,f"{i:04d} Contact",now,now) for i in range(1001)])
            conn.commit()
        found=search_work_memory(self.user,"1000 Contact")
        self.assertEqual(found["contacts"][0]["full_name"],"1000 Contact")
        self.assertFalse(search_work_memory(self.other,"1000 Contact")["contacts"])

    def test_old_memory_edit_and_keyset_pages_survive_new_insert(self):
        ids=[]
        for i in range(1001): ids.append(upsert_memory(self.user,"fact",f"fact.{i}",f"value {i}",1,source_type="user_correction",source_id=str(i))["memory_id"])
        result=self.client.patch(f"/api/memory/{ids[0]}",json={"value":"corrected"})
        self.assertEqual(result.status_code,200)
        seen=set(); before=None
        while True:
            data=self.client.get('/api/memory/controls'+(f'?before={before}' if before else '')).get_json()
            page={x["memory_id"] for x in data["memories"]}
            self.assertFalse(page & seen);seen |= page
            if before is None: upsert_memory(self.user,"fact","inserted.during.paging","new",1,source_type="user_correction",source_id="new")
            before=data["next_cursor"]
            if not before: break
        self.assertEqual(seen,set(ids))
        with self.client.session_transaction() as session: session["user_id"]=self.other
        self.assertEqual(self.client.patch(f"/api/memory/{ids[0]}",json={"value":"wrong"}).status_code,404)

    def test_relation_search_finds_old_company_contact_and_history(self):
        company=create_company(self.user,"Ёлка")
        from core.memory_store import create_contact,record_interaction
        create_contact(self.user,"Customer",company_id=company["company_id"])
        record_interaction(self.user,"meeting","Old proposal",company_id=company["company_id"])
        data=search_work_memory(self.user,"ЕЛКА")
        self.assertEqual(len(data["contacts"]),1);self.assertEqual(len(data["interactions"]),1)

    def test_action_is_confirmed_idempotent_and_completion_is_shared(self):
        item=create_commitment(self.user,"Send proposal")
        path=f"/api/memory/commitments/{item['commitment_id']}/actions"
        self.assertEqual(self.client.post(path,json={"kind":"task"}).status_code,400)
        first=self.client.post(path,json={"kind":"task","confirmed":True}).get_json()["action"]
        second=self.client.post(path,json={"kind":"task","confirmed":True}).get_json()["action"]
        self.assertEqual(first["target_id"],second["target_id"])
        self.assertEqual(len(list_planner_tasks(self.user)),1)
        from modules.task_service import complete_task
        complete_task(self.user,first["target_id"])
        self.assertEqual(get_commitment(self.user,item["commitment_id"])["status"],"done")
        self.client.patch(f"/api/tasks/{first['target_id']}",json={"status":"open"})
        self.assertEqual(get_commitment(self.user,item["commitment_id"])["status"],"open")

    def test_reminder_next_step_requires_future_time_and_is_linked(self):
        item=create_commitment(self.user,"Call client")
        path=f"/api/memory/commitments/{item['commitment_id']}/actions"
        self.assertEqual(self.client.post(path,json={"kind":"reminder","confirmed":True}).status_code,400)
        due=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        response=self.client.post(path,json={"kind":"reminder","confirmed":True,"due_at":due})
        self.assertEqual(response.status_code,201)
        identifier=response.get_json()["action"]["target_id"]
        complete_reminder(self.user,identifier)
        self.assertEqual(get_commitment(self.user,item["commitment_id"])["status"],"done")

    def test_due_time_uses_account_timezone_and_deleted_action_can_be_replaced(self):
        from core.db import save_user_timezone
        from core.reminder_store import delete_saved_reminder
        from core.search_api_store import memory_entity
        save_user_timezone(self.user,"Europe/Moscow")
        item=create_commitment(self.user,"Local time call")
        path=f"/api/memory/commitments/{item['commitment_id']}/actions"
        local=(datetime.now(timezone.utc)+timedelta(days=2)).replace(hour=12,minute=0,second=0,microsecond=0,tzinfo=None)
        payload={"kind":"reminder","confirmed":True,"due_at":local.isoformat()}
        first=self.client.post(path,json=payload).get_json()["action"]
        actual=datetime.fromisoformat(memory_entity(self.user,"reminder",first["target_id"])["remind_at"])
        self.assertEqual(actual.hour,9)
        self.assertTrue(delete_saved_reminder(self.user,first["target_id"]))
        second=self.client.post(path,json=payload).get_json()["action"]
        self.assertNotEqual(first["target_id"],second["target_id"])

    def test_legacy_task_completion_and_delete_share_commitment_links(self):
        from core.db import set_task_completed,delete_task
        item=create_commitment(self.user,"Shared task")
        path=f"/api/memory/commitments/{item['commitment_id']}/actions"
        payload={"kind":"task","confirmed":True}
        first=self.client.post(path,json=payload).get_json()["action"]
        set_task_completed(self.user,first["target_id"])
        self.assertEqual(get_commitment(self.user,item["commitment_id"])["status"],"done")
        set_task_completed(self.user,first["target_id"],False)
        delete_task(self.user,first["target_id"])
        second=self.client.post(path,json=payload).get_json()["action"]
        self.assertNotEqual(first["target_id"],second["target_id"])

    def test_other_users_commitment_cannot_create_actions(self):
        item=create_commitment(self.other,"Private proposal")
        self.assertEqual(self.client.post(f"/api/memory/commitments/{item['commitment_id']}/actions",json={"kind":"task","confirmed":True}).status_code,404)
        self.assertFalse(list_planner_tasks(self.user))

    def test_action_rolls_back_created_task_if_followup_write_fails(self):
        item=create_commitment(self.user,"Atomic proposal")
        from modules import commitment_actions
        real=commitment_actions.create_planner_task
        def create_then_fail(*args,**kwargs):
            real(*args,**kwargs)
            raise RuntimeError("simulated failure after task INSERT")
        with patch.object(commitment_actions,"create_planner_task",side_effect=create_then_fail):
            with self.assertRaises(RuntimeError): commitment_actions.create_commitment_action(self.user,item["commitment_id"],{"kind":"task","confirmed":True})
        self.assertFalse(list_planner_tasks(self.user))

    def test_global_search_matches_old_records_and_selection_uses_context(self):
        from core.note_store import create_note
        from core.task_planner_store import create_planner_task
        from core.conversation_context import current_entity,user_state
        from core.web_transport import WebContext
        note=create_note(self.user,"Needle first note")
        create_note(self.other,"Needle private")
        create_planner_task(self.user,"Needle task")
        now=datetime.now(timezone.utc).isoformat()
        with db_lock:
            conn.executemany("INSERT INTO notes(user_id,title,normalized_title,text,normalized_text,created_at,updated_at) VALUES (?,?,'later',?,'later',?,?)",[(self.user,f'Later {i}',f'Later {i}',now,now) for i in range(1001)])
            conn.executemany("INSERT INTO tasks(user_id,title,status,priority,created_at) VALUES (?,?,'open','normal',?)",[(self.user,f'Later {i}',now) for i in range(1001)])
            conn.commit()
        data=self.client.get('/api/search?q=needle').get_json()
        self.assertEqual(len(data["groups"]["note"]["items"]),1)
        self.assertEqual(len(data["groups"]["task"]["items"]),1)
        self.assertEqual(self.client.post('/api/search/open',json={"kind":"note","id":note["note_id"]}).status_code,200)
        self.assertEqual(current_entity(WebContext(user_state(self.user)),"note")["id"],str(note["note_id"]))
        self.assertEqual(self.client.post('/api/search/open',json={"kind":"note","id":999999999}).status_code,404)

    def test_internal_monitor_authentication_and_live_ai_state(self):
        ai._set_provider_state("degraded","rate_limited",3)
        settings=ai.AISettings(True,"gigachat","model","test-only","scope","https://api.example","https://auth.example",5,100,None)
        self.assertEqual(self.client.get('/internal/runtime').status_code,403)
        with patch.object(ai,"load_ai_settings",return_value=settings):
            response=self.client.get('/internal/runtime',headers={"X-Monitor-Token":monitor_token()})
            self.assertEqual(response.get_json()["ai"]["state"],"degraded")
            self.assertEqual(response.get_json()["sha"],RUNNING_SHA)
            self.assertEqual(self.client.get('/internal/runtime',headers={"X-Monitor-Token":monitor_token()},environ_overrides={"REMOTE_ADDR":"203.0.113.5"}).status_code,403)
        ai._reset_token_cache_for_tests()

    def test_stale_ai_observation_and_monitor_token_destination(self):
        from scripts.production_smoke import _running_status
        settings=ai.AISettings(True,"gigachat","model","test-only","scope","https://api.example","https://auth.example",5,100,None)
        ai._set_provider_state("healthy")
        with patch.object(ai,"load_ai_settings",return_value=settings), patch.object(ai.time,"time",return_value=ai._provider_status_snapshot()["updated_at"]+3601):
            status=ai.get_ai_status()
            self.assertEqual(status["state"],"unknown")
            self.assertIsNotNone(status["last_success_at"])
        with patch.dict(os.environ,{"RUNTIME_STATUS_URL":"http://example.test/internal/runtime"}), patch('scripts.production_smoke.requests.Session') as client:
            with self.assertRaises(ValueError):_running_status()
            client.assert_not_called()
        ai._reset_token_cache_for_tests()

    def test_separate_monitor_process_reads_running_app_state(self):
        from config import WEB_SESSION_SECRET
        from werkzeug.serving import make_server
        settings=ai.AISettings(True,"gigachat","model","test-only","scope","https://api.example","https://auth.example",5,100,None)
        ai._set_provider_state("degraded","transport_error")
        server=make_server('127.0.0.1',0,self.app,threaded=True)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        environment={**os.environ,"WEB_SESSION_SECRET":WEB_SESSION_SECRET,"RUNTIME_STATUS_URL":f'http://127.0.0.1:{server.server_port}/internal/runtime'}
        try:
            with patch.object(ai,"load_ai_settings",return_value=settings):
                process=subprocess.run([sys.executable,'-c','import json; from scripts.production_smoke import _running_status; print(json.dumps(_running_status()))'],cwd=Path(__file__).resolve().parents[1],env=environment,text=True,capture_output=True,timeout=15,check=True)
            report=json.loads(process.stdout)
            self.assertEqual(report['ai']['state'],'degraded')
            self.assertEqual(report['ai']['last_error'],'transport_error')
            self.assertEqual(report['sha'],RUNNING_SHA)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=2)
            ai._reset_token_cache_for_tests()


class ErasureRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=self.root/'data'/'bot.db';self.db.parent.mkdir()
        self.connection=sqlite3.connect(self.db)
        self.connection.execute("CREATE TABLE google_accounts(user_id INTEGER PRIMARY KEY,google_sub TEXT,created_at TEXT)")
        self.connection.execute("CREATE TABLE notes(user_id INTEGER,text TEXT)")
        stamp=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        self.connection.executemany("INSERT INTO google_accounts VALUES (?,?,?)",[(1,"deleted-subject",stamp),(2,"retained-subject",stamp)])
        self.connection.executemany("INSERT INTO notes VALUES (?,?)",[(1,"deleted note"),(2,"retained note")]);self.connection.commit()
        enforce_erasure_journal(self.connection,self.db)
        with patch.dict(os.environ,{"DB_PATH":str(self.db),"BACKUP_DIR":str(self.root/'backups'),"WEB_PUSH_VAPID_PRIVATE_KEY":str(self.root/'absent.pem')},clear=False): self.snapshot=create_backup()

    def tearDown(self): self.connection.close();self.temp.cleanup()

    def test_backup_delete_restore_erases_old_incarnation_and_preserves_other_user(self):
        record_erasure(self.connection,self.db,1)
        self.connection.close()
        result=restore_snapshot(self.snapshot,self.db)
        self.assertEqual(result["erased_accounts"],1)
        with sqlite3.connect(self.db) as restored:
            self.assertEqual(restored.execute('SELECT user_id FROM google_accounts').fetchall(),[(2,)])
            self.assertEqual(restored.execute('SELECT user_id FROM notes').fetchall(),[(2,)])

    def test_new_signup_and_reused_id_are_not_erased(self):
        record_erasure(self.connection,self.db,1)
        self.connection.execute('UPDATE google_accounts SET created_at=? WHERE user_id=1',((datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat(),));self.connection.commit()
        self.assertEqual(enforce_erasure_journal(self.connection,self.db),0)
        self.connection.execute("UPDATE google_accounts SET google_sub='different-subject',created_at=? WHERE user_id=1",((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),));self.connection.commit()
        self.assertEqual(enforce_erasure_journal(self.connection,self.db),0)

    def test_missing_journal_refuses_startup_and_restore_without_replacing_database(self):
        journal_path(self.db).unlink()
        with self.assertRaises(RuntimeError): enforce_erasure_journal(self.connection,self.db)
        before=self.db.read_bytes()
        with self.assertRaises(RuntimeError): restore_snapshot(self.snapshot,self.db)
        self.assertEqual(self.db.read_bytes(),before)

    def test_wrong_journal_refuses_restore_and_crash_after_receipt_is_repaired(self):
        record_erasure(self.connection,self.db,1)
        # Simulate a crash before the account DELETE transaction committed.
        self.assertEqual(enforce_erasure_journal(self.connection,self.db),1)
        wrong=self.root/'other.db'
        with sqlite3.connect(wrong) as other:enforce_erasure_journal(other,wrong)
        before=self.db.read_bytes()
        with self.assertRaises(RuntimeError):restore_snapshot(self.snapshot,self.db,journal=journal_path(wrong))
        self.assertEqual(before,self.db.read_bytes())

    def test_checksum_mismatch_and_live_runtime_lock_refuse_restore(self):
        descriptor=runtime_lock(self.db)
        try:
            with self.assertRaises(RuntimeError): restore_snapshot(self.snapshot,self.db)
        finally: os.close(descriptor)
        with (self.snapshot/'bot.db').open('ab') as handle:handle.write(b'corrupt')
        with self.assertRaises(RuntimeError): restore_snapshot(self.snapshot,self.db)
