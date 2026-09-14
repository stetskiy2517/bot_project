import unittest

from integrations.speech import normalize_time_format
from modules.calendar import _extract_title


class VoiceMeetingNormalizationTests(unittest.TestCase):
    def test_bare_meeting_with_client_asr_variant_is_singularized(self):
        text = normalize_time_format("Встречи с клиентом завтра в 15")
        self.assertEqual(text, "Встреча с клиентом завтра в 15")
        self.assertEqual(_extract_title(text), "Встреча с клиентом")

    def test_plural_query_is_not_changed(self):
        text = normalize_time_format("Покажи встречи с клиентом на этой неделе")
        self.assertEqual(text, "Покажи встречи с клиентом на этой неделе")

    def test_prefixed_phrase_is_not_overcorrected(self):
        text = normalize_time_format("Запланируй встречи с клиентом завтра в 15")
        self.assertEqual(text, "Запланируй встречи с клиентом завтра в 15")

    def test_existing_time_normalization_still_works(self):
        text = normalize_time_format("Встреча с клиентом завтра в 15.30")
        self.assertEqual(text, "Встреча с клиентом завтра в 15:30")


if __name__ == "__main__":
    unittest.main()
