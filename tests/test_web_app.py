import unittest
from unittest.mock import patch
import web_app

class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.app = web_app.create_web_app(); self.client = self.app.test_client()
    def _register(self, client, email, name="User"):
        return client.post("/api/register", json={"email": email, "password": "strongpass123", "name": name})
    def test_health_does_not_require_telegram(self):
        r=self.client.get("/api/health"); self.assertEqual(r.status_code,200); self.assertEqual(r.get_json(),{"status":"ok","transport":"web"})
    def test_private_api_requires_account(self):
        self.assertEqual(self.client.get("/api/status").status_code,401); self.assertEqual(self.client.post("/api/chat",json={"message":"test"}).status_code,401)
    def test_registration_creates_independent_sessions(self):
        a=self.app.test_client(); b=self.app.test_client(); ra=self._register(a,"alice@example.test","Alice"); rb=self._register(b,"bob@example.test","Bob")
        self.assertEqual(ra.status_code,201); self.assertEqual(rb.status_code,201); self.assertNotEqual(ra.get_json()["user_id"],rb.get_json()["user_id"])
        self.assertEqual(a.get("/api/status").get_json()["user"]["email"],"alice@example.test"); self.assertEqual(b.get("/api/status").get_json()["user"]["email"],"bob@example.test")
    def test_settings_are_isolated_between_users(self):
        a=self.app.test_client(); b=self.app.test_client(); self._register(a,"settings-a@example.test","A"); self._register(b,"settings-b@example.test","B")
        a.post("/api/settings",json={"timezone":"Europe/Tallinn","buffer_minutes":45}); b.post("/api/settings",json={"timezone":"Europe/Moscow","buffer_minutes":5})
        sa=a.get("/api/status").get_json(); sb=b.get("/api/status").get_json(); self.assertEqual(sa["timezone"],"Europe/Tallinn"); self.assertEqual(sb["timezone"],"Europe/Moscow"); self.assertEqual(sa["preferences"]["buffer_minutes"],45); self.assertEqual(sb["preferences"]["buffer_minutes"],5)
    def test_chat_routes_with_session_user_id(self):
        a=self.app.test_client(); b=self.app.test_client(); aid=self._register(a,"chat-a@example.test","Alice").get_json()["user_id"]; bid=self._register(b,"chat-b@example.test","Bob").get_json()["user_id"]; seen=[]
        async def fake_route(update,context,text=None): seen.append((update.effective_user.id,update.effective_user.full_name,text)); await update.message.reply_text("ok"); return True
        with patch("web_app.route_text",side_effect=fake_route): a.post("/api/chat",json={"message":"A"}); b.post("/api/chat",json={"message":"B"})
        self.assertEqual(seen,[(aid,"Alice","A"),(bid,"Bob","B")])
    def test_google_auth_uses_session_user(self):
        self._register(self.client,"oauth-user@example.test","OAuth User"); uid=self.client.get("/api/status").get_json()["user"]["id"]
        with patch("web_app.build_authorization_url",return_value="https://accounts.google.test/auth") as build:
            r=self.client.get("/api/google/auth")
        self.assertEqual(r.status_code,200); build.assert_called_once_with(uid)
    def test_duplicate_email_rejected_and_login_works(self):
        self.assertEqual(self._register(self.client,"login@example.test").status_code,201); self.client.post("/api/logout"); self.assertEqual(self._register(self.app.test_client(),"login@example.test").status_code,400)
        self.assertEqual(self.client.post("/api/login",json={"email":"login@example.test","password":"wrong"}).status_code,401); self.assertEqual(self.client.post("/api/login",json={"email":"login@example.test","password":"strongpass123"}).status_code,200)
    def test_pwa_shell_and_manifest_are_served(self):
        self.assertEqual(self.client.get("/").status_code,200); self.assertEqual(self.client.get("/manifest.webmanifest").status_code,200)

if __name__ == "__main__": unittest.main()
