from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_attention_refreshes_review_before_loading_saved_items():
    script = (ROOT / "web" / "attention-center.js").read_text(encoding="utf-8")
    refresh_call = script.index("await refreshReviewContext();")
    attention_call = script.index('const data = await api("/api/mobile/attention")')
    assert refresh_call < attention_call
    assert 'api("/api/mobile/today")' in script


def test_attention_marks_ai_review_but_not_free_review():
    script = (ROOT / "web" / "attention-center.js").read_text(encoding="utf-8")
    assert 'review?.presentation === "ai"' in script
    assert 'item.source_type === "daily_review"' in script
    assert "AI-сводка" in script


def test_attention_preserves_briefing_paragraphs():
    css = (ROOT / "web" / "attention-center.css").read_text(encoding="utf-8")
    assert ".mobile-attention-body" in css
    assert "white-space: pre-line" in css
