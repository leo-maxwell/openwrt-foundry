import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (ROOT / ".env.example").read_text()


class CredentialTests(unittest.TestCase):
    def render(self, name=None, value=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = Path(temp.name) / ".env"
        lines = SAMPLE.splitlines()
        if name:
            lines = [f"{name}={value}" if line.startswith(name + "=") else line for line in lines]
        env.write_text("\n".join(lines) + "\n")
        output = env.parent / "files"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/parser.py"), "--env", str(env), "--output", str(output)],
            capture_output=True, text=True,
        )
        return result, output

    def test_invalid_credentials_leave_no_output(self):
        for name, value in [
            ("SSH_AUTHORIZED_KEYS", '["invalid-key"]'),
            ("SSH_AUTHORIZED_KEYS", '["ssh-ed25519 ZmFrZQ=="]'),
            ("ADMIN_PASSWORD_HASH", "password"),
            ("ADMIN_PASSWORD_HASH", "$6$salt$short"),
        ]:
            with self.subTest(name=name, value=value):
                result, output = self.render(name, value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)
                self.assertNotIn(value, result.stderr)
                self.assertFalse(output.exists())

    def test_example_credentials_render(self):
        result, output = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = next(line for line in SAMPLE.splitlines() if line.startswith("SSH_AUTHORIZED_KEYS="))
        keys = json.loads(entry.partition("=")[2])
        self.assertEqual((output / "etc/dropbear/authorized_keys").read_text().splitlines(), keys)
