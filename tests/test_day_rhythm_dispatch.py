from contextlib import nullcontext


def _prepare_dispatch(monkeypatch, *, push_users, review_users):
    import modules.reminder_dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "list_push_user_ids", lambda: list(push_users))
    monkeypatch.setattr(dispatcher, "list_scheduled_review_user_ids", lambda: list(review_users))
    monkeypatch.setattr(dispatcher, "get_google_account", lambda _user_id: {"connected": True})
    monkeypatch.setattr(dispatcher, "quiet_until", lambda _user_id, _now: None)
    monkeypatch.setattr(dispatcher, "user_operation", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(dispatcher, "claim_due_for_push", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(dispatcher, "claim_repeat_attempts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(dispatcher, "_dispatch_attention_pushes", lambda *_args, **_kwargs: (0, 0))
    return dispatcher


def test_review_only_user_is_evaluated_without_push(monkeypatch):
    dispatcher = _prepare_dispatch(monkeypatch, push_users=[], review_users=[77])
    calls = []

    def deliver(user_id, sender, *, now=None):
        calls.append((user_id, sender(user_id, "review", "tag"), now is not None))
        return 0

    monkeypatch.setattr(dispatcher, "deliver_reviews_for_user", deliver)

    dispatcher.dispatch_due_reminders_once()

    assert calls == [(77, False, True)]


def test_push_and_review_user_is_processed_once(monkeypatch):
    dispatcher = _prepare_dispatch(monkeypatch, push_users=[42], review_users=[42])
    review_calls = []
    claim_calls = []

    monkeypatch.setattr(
        dispatcher,
        "claim_due_for_push",
        lambda user_ids, **_kwargs: claim_calls.append(tuple(user_ids)) or [],
    )
    monkeypatch.setattr(
        dispatcher,
        "deliver_reviews_for_user",
        lambda user_id, _sender, *, now=None: review_calls.append(user_id) or 0,
    )

    dispatcher.dispatch_due_reminders_once()

    assert claim_calls == [(42,)]
    assert review_calls == [42]


def test_reminder_limit_does_not_skip_review_users(monkeypatch):
    dispatcher = _prepare_dispatch(monkeypatch, push_users=[1], review_users=[2])
    review_calls = []

    monkeypatch.setattr(
        dispatcher,
        "claim_due_for_push",
        lambda *_args, **_kwargs: [{"reminder_id": index} for index in range(1, 3)],
    )
    monkeypatch.setattr(dispatcher, "get_saved_reminder", lambda *_args: None)
    monkeypatch.setattr(
        dispatcher,
        "deliver_reviews_for_user",
        lambda user_id, _sender, *, now=None: review_calls.append(user_id) or 0,
    )

    dispatcher.dispatch_due_reminders_once(limit=2)

    assert review_calls == [2]
