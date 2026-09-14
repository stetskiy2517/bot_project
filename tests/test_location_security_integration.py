from __future__ import annotations

import math
import unittest
import uuid

from core.db import get_or_create_google_user
from core.location_context import (
    clear_current_location, get_current_location, save_current_location,
)
from modules.account_privacy import create_erase_challenge, erase_account, ERASE_CONFIRMATION
from tests.web_test_support import web_test_app


class LocationSecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        label = uuid.uuid4().hex
        self.user = get_or_create_google_user(label, label + '@example.test', 'Location test')
        self.app = web_test_app()
        self.client = self.app.test_client()
        with self.client.session_transaction() as stored:
            stored['user_id'] = self.user

    def tearDown(self):
        clear_current_location(self.user)

    def test_location_accepts_authenticated_request_with_csrf(self):
        result = self.client.post('/api/location', json={
            'latitude': 55.78, 'longitude': 37.63, 'accuracy': 25,
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(get_current_location(self.user).latitude, 55.78)

    def test_logout_clears_live_location(self):
        save_current_location(self.user, 55.78, 37.63)
        self.assertEqual(self.client.post('/api/logout', json={}).status_code, 200)
        self.assertIsNone(get_current_location(self.user))

    def test_erasure_clears_live_location_but_not_other_users(self):
        save_current_location(self.user, 55.78, 37.63)
        other = self.user + 1_000_000
        save_current_location(other, 50, 30)
        try:
            erase_account(self.user, create_erase_challenge(self.user), ERASE_CONFIRMATION)
            self.assertIsNone(get_current_location(self.user))
            self.assertIsNotNone(get_current_location(other))
        finally:
            clear_current_location(other)

    def test_invalid_accuracy_is_rejected_without_overwriting_position(self):
        original = save_current_location(self.user, 55.78, 37.63, 20)
        for value in (math.nan, math.inf, -1, True):
            with self.subTest(value=value):
                result = self.client.post('/api/location', json={
                    'latitude': 50, 'longitude': 30, 'accuracy': value,
                })
                self.assertEqual(result.status_code, 400)
                self.assertEqual(get_current_location(self.user), original)

    def test_invalid_coordinates_are_rejected(self):
        for value in (math.nan, math.inf, True, 91, -91):
            with self.subTest(value=value), self.assertRaises(ValueError):
                save_current_location(self.user, value, 30)

    def test_helper_is_loaded_before_location_script(self):
        body = self.client.get('/').get_data(as_text=True)
        self.assertLess(body.index('src="/reliability.js"'), body.index('src="/location.js"'))
        self.assertIn('src="/assistant.js"', body)
