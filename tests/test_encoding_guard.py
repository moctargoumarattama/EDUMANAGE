from pathlib import Path
import unittest


class EncodingGuardTestCase(unittest.TestCase):
    def test_app_text_files_do_not_contain_mojibake_markers(self):
        markers = (
            "\u00c3",
            "\u00c2",
            "\u00e2\u20ac",
            "\u00e2\u2030",
            "\u00e2\u2020",
            "\ufffd",
        )
        suffixes = {".py", ".html", ".css", ".js", ".md", ".txt"}
        offenders = []

        for path in Path("app").rglob("*"):
            if path.suffix.lower() not in suffixes:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                offenders.append(f"{path}: not valid UTF-8")
                continue
            for marker in markers:
                if marker in text:
                    offenders.append(f"{path}: contains {marker.encode('unicode_escape').decode()}")
                    break

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
