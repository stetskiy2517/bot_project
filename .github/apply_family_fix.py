from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "core/db.py",
    '    "travel": "7",\n    "personal": "5",\n',
    '    "travel": "7",\n    "family": "4",\n    "personal": "5",\n',
)

replace_once(
    "modules/calendar.py",
    '    "travel": {"color_id": "7", "keywords": ("самолет", "самолёт", "рейс", "поезд", "вокзал", "аэропорт", "дорог", "такси", "перелет", "перелёт", "командиров", "отъезд", "прилет", "прилёт")},\n    "personal": {"color_id": "5", "keywords": ("личн", "дом", "покуп", "магазин", "семья", "родител", "ребен", "ребён", "день рождения", "забрать", "отвезти")},\n}\n\n\ndef _normalise',
    '    "travel": {"color_id": "7", "keywords": ("самолет", "самолёт", "рейс", "поезд", "вокзал", "аэропорт", "дорог", "такси", "перелет", "перелёт", "командиров", "отъезд", "прилет", "прилёт")},\n    "family": {"color_id": "4", "keywords": ()},\n    "personal": {"color_id": "5", "keywords": ("личн", "дом", "покуп", "купить", "куплю", "купи", "магазин", "день рождения", "забрать", "отвезти")},\n}\nFAMILY_RE = re.compile(\n    r"\\b(?:семь(?:я|и|е|ю|ей|ям|ями|ях)|семейн\\w*|родител\\w*|ребен\\w*|"\n    r"дет(?:и|ей|ям|ьми|ях)|сын\\w*|доч\\w*|мам\\w*|пап\\w*)\\b",\n    re.IGNORECASE,\n)\n\n\ndef _normalise',
)

replace_once(
    "modules/calendar.py",
    '    if re.search(r"\\bсемь(?:я|и|е|ю|ей|ям|ями|ях)\\b", lower):\n        return "personal", colors.get("personal")\n',
    '    if FAMILY_RE.search(lower):\n        return "family", colors.get("family")\n',
)

replace_once(
    "web/index.html",
    '        travel: "Поездки",\n        personal: "Личное",\n',
    '        travel: "Поездки",\n        family: "Семья",\n        personal: "Личное",\n',
)
replace_once(
    "web/index.html",
    '        travel: "7",\n        personal: "5",\n',
    '        travel: "7",\n        family: "4",\n        personal: "5",\n',
)

replace_once(
    "web_app.py",
    '                if not isinstance(colors, dict) or set(colors) != set(DEFAULT_CATEGORY_COLORS):\n                    raise ValueError("Неверный набор категорий")\n                parsed_colors = {}\n                for category, color_id in colors.items():\n',
    '                if not isinstance(colors, dict) or not colors:\n                    raise ValueError("Неверный набор категорий")\n                unknown_categories = set(colors) - set(DEFAULT_CATEGORY_COLORS)\n                if unknown_categories:\n                    raise ValueError("Неверный набор категорий")\n                parsed_colors = dict(_status_payload(user_id)["preferences"]["category_colors"])\n                for category, color_id in colors.items():\n',
)

replace_once(
    "tests/test_calendar_regression_audit.py",
    '    def test_family_is_personal_not_rest(self):\n        self.assertEqual(_detect_category("ужин с семьей"), ("personal", "5"))\n',
    '    def test_family_has_own_category(self):\n        self.assertEqual(_detect_category("ужин с семьей"), ("family", "4"))\n',
)
replace_once(
    "tests/test_calendar_regression_audit.py",
    '        colors = {"work": "9", "health": "11", "rest": "2", "travel": "7", "personal": "5", "other": None}\n',
    '        colors = {"work": "9", "health": "11", "rest": "2", "travel": "7", "family": "4", "personal": "5", "other": None}\n',
)

replace_once(
    "tests/test_web_app.py",
    '            "work": "9", "health": "11", "rest": "2",\n            "travel": "7", "personal": "5", "other": None,\n',
    '            "work": "9", "health": "11", "rest": "2",\n            "travel": "7", "family": "4", "personal": "5", "other": None,\n',
)
replace_once(
    "tests/test_web_app.py",
    '            "work": "99", "health": "6", "rest": "10",\n            "travel": "7", "personal": "5", "other": None,\n',
    '            "work": "99", "health": "6", "rest": "10",\n            "travel": "7", "family": "4", "personal": "5", "other": None,\n',
)

Path("tests/test_category_beta_matrix.py").write_text(
    '''from datetime import datetime\nfrom pathlib import Path\nimport unittest\nfrom zoneinfo import ZoneInfo\n\nimport web_app\nfrom core.db import DEFAULT_CATEGORY_COLORS, get_or_create_google_user\nfrom modules.calendar import EVENT_CATEGORIES, _detect_category\nfrom modules.calendar_event_features import build_all_day_event\n\n\nclass CategoryBetaMatrixTests(unittest.TestCase):\n    def test_backend_category_sets_do_not_drift(self):\n        self.assertEqual(set(EVENT_CATEGORIES), set(DEFAULT_CATEGORY_COLORS) - {"other"})\n\n    def test_family_language_matrix(self):\n        cases = [\n            "ужин с семьей",\n            "встреча с семьёй завтра в 19",\n            "забрать ребенка из школы",\n            "поездка с детьми",\n            "ужин с родителями",\n            "день рождения дочери",\n            "позвонить маме",\n            "семейный ужин",\n        ]\n        for text in cases:\n            with self.subTest(text=text):\n                self.assertEqual(_detect_category(text)[0], "family")\n\n    def test_family_priority_over_generic_work_and_travel_words(self):\n        self.assertEqual(_detect_category("встреча с родителями")[0], "family")\n        self.assertEqual(_detect_category("поездка с детьми")[0], "family")\n\n    def test_number_seven_does_not_trigger_family(self):\n        self.assertNotEqual(_detect_category("семь звонков клиентам")[0], "family")\n\n    def test_other_categories_still_work(self):\n        cases = {\n            "рабочая встреча с клиентом": "work",\n            "прием у невролога": "health",\n            "кино с друзьями": "rest",\n            "рейс в Москву": "travel",\n            "купить продукты": "personal",\n            "непонятное событие": "other",\n        }\n        for text, expected in cases.items():\n            with self.subTest(text=text):\n                self.assertEqual(_detect_category(text)[0], expected)\n\n    def test_family_uses_user_color_override(self):\n        colors = dict(DEFAULT_CATEGORY_COLORS)\n        colors["family"] = "11"\n        self.assertEqual(_detect_category("ужин с родителями", colors), ("family", "11"))\n\n    def test_family_color_applies_to_all_day_event(self):\n        now = datetime(2026, 9, 12, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))\n        event = build_all_day_event(\n            "день рождения дочери завтра",\n            "Europe/Moscow",\n            now,\n            category_colors=DEFAULT_CATEGORY_COLORS,\n        )\n        self.assertIsNotNone(event)\n        self.assertEqual(event["colorId"], "4")\n        self.assertIn("category: family", event["description"])\n\n    def test_web_settings_expose_family(self):\n        html = Path("web/index.html").read_text(encoding="utf-8")\n        self.assertIn('family: "Семья"', html)\n        self.assertIn('family: "4"', html)\n\n\nclass CategorySettingsCompatibilityTests(unittest.TestCase):\n    def setUp(self):\n        self.app = web_app.create_web_app()\n        self.client = self.app.test_client()\n        suffix = self._testMethodName\n        uid = get_or_create_google_user(\n            f"beta-family-compat-{suffix}",\n            f"beta-family-compat-{suffix}@example.test",\n            "Beta Family",\n        )\n        with self.client.session_transaction() as session:\n            session["user_id"] = uid\n\n    def test_legacy_color_payload_without_family_remains_valid(self):\n        legacy = {\n            "work": "3",\n            "health": "6",\n            "rest": "10",\n            "travel": "7",\n            "personal": "5",\n            "other": None,\n        }\n        response = self.client.post("/api/settings", json={"category_colors": legacy})\n        self.assertEqual(response.status_code, 200)\n        saved = response.get_json()["preferences"]["category_colors"]\n        self.assertEqual(saved["family"], "4")\n        for category, color in legacy.items():\n            self.assertEqual(saved[category], color)\n\n    def test_family_color_can_be_changed(self):\n        response = self.client.post("/api/settings", json={"category_colors": {"family": "11"}})\n        self.assertEqual(response.status_code, 200)\n        self.assertEqual(response.get_json()["preferences"]["category_colors"]["family"], "11")\n\n    def test_unknown_category_is_rejected(self):\n        response = self.client.post("/api/settings", json={"category_colors": {"alien": "1"}})\n        self.assertEqual(response.status_code, 400)\n        self.assertEqual(response.get_json()["error"], "invalid_settings")\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
    encoding="utf-8",
)
