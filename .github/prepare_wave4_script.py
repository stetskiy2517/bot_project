from pathlib import Path

path = Path('.github/apply_wave4_parser_fix.py')
text = path.read_text(encoding='utf-8')
old = '''replace_once(
    "modules/calendar.py",
    '        parsed = dateparser.parse(\\n            date_match.group(0),\\n',
    '        date_text = re.sub(r"(\\\\d{1,2})-?го\\\\b", r"\\\\1", date_match.group(0), flags=re.IGNORECASE)\\n        parsed = dateparser.parse(\\n            date_text,\\n',
)
'''
new = '''replace_once(
    "modules/calendar.py",
    '        parsed = dateparser.parse(\\n            named_match.group(0),\\n',
    '        date_text = re.sub(r"(\\\\d{1,2})-?го\\\\b", r"\\\\1", named_match.group(0), flags=re.IGNORECASE)\\n        parsed = dateparser.parse(\\n            date_text,\\n',
)
'''
if old not in text:
    raise RuntimeError('wave4 patch target not found')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
