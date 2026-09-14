"""Validate JavaScript assets, including scripts embedded in HTML."""
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import tempfile


class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.active = "src" not in attrs and attrs.get("type", "") in {
                "", "text/javascript", "application/javascript", "module",
            }
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.active = False


def main():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "web").glob("*.js"):
        subprocess.run(["node", "--check", str(path)], check=True)
    for path in (root / "web").glob("*.html"):
        parser = InlineScripts()
        parser.feed(path.read_text(encoding="utf-8"))
        for script in parser.scripts:
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as stream:
                stream.write(script)
                stream.flush()
                subprocess.run(["node", "--check", stream.name], check=True)
    print("JavaScript and inline scripts: OK")


if __name__ == "__main__":
    main()
