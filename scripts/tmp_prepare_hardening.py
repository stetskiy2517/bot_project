from pathlib import Path

path = Path("scripts/tmp_harden_navigation.py")
text = path.read_text(encoding="utf-8")
old = 'ors = insert_before(ors, "def geocode(address: str) -> tuple[float, float]:",'
new = 'ors = insert_before(ors, "@lru_cache(maxsize=512)\\ndef geocode(address: str) -> tuple[float, float]:",'
if old not in text:
    raise RuntimeError("ORS insertion marker not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
