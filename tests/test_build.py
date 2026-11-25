import gzip
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build


class BuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.files = self.work / "files"
        profile = build.load_profile("stable")
        self.inputs = build.parse_inputs(profile, ROOT / ".env.example")
        build.render_files(profile["files"], self.inputs, self.files)

    def test_local_entry_point(self):
        result = subprocess.run([ROOT / "build.sh", "--check", "--env", ROOT / ".env.example"],
                                cwd=self.work, capture_output=True, text=True, check=True)
        self.assertIn(self.inputs["OPENWRT_DEVICE_PROFILE"], result.stdout)

    def test_reject_wrong_builder_checksum(self):
        with patch.object(build, "urlopen", return_value=io.BytesIO(b"wrong archive")):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                build.unpack_builder("https://example.org/archive", "0" * 64, self.work)
        self.assertFalse((self.work / "builder").exists())

    def test_truncated_image(self):
        image = self.work / "image.gz"
        image.write_bytes(gzip.compress(b"payload")[:-1])
        with self.assertRaisesRegex(ValueError, "truncated"):
            build.unpack_disk(image, self.work / "disk")

    def make_fixture(self):
        device = self.inputs["OPENWRT_DEVICE_PROFILE"]
        target = self.inputs["OPENWRT_TARGET"]
        release = "25.12.6"
        builder = self.work / "fixture-builder"
        builder.mkdir()
        fixture = builder / "root"
        shutil.copytree(self.files, fixture)
        (fixture / "etc/openwrt_release").write_text(
            f"DISTRIB_RELEASE='{release}'\nDISTRIB_TARGET='{target}'\n"
        )
        subprocess.run(["mksquashfs", fixture, builder / "root.sqfs", "-noappend", "-no-xattrs"],
                       check=True, stdout=subprocess.DEVNULL)
        payload = (builder / "root.sqfs").read_bytes()
        mbr = bytearray(512)
        mbr[510:] = b"\x55\xaa"
        mbr[450] = 0x83
        mbr[454:458] = (1).to_bytes(4, "little")
        mbr[458:462] = (len(payload) // 512).to_bytes(4, "little")
        name = f"openwrt-{release}-rockchip-armv8-{device}-squashfs-sysupgrade.img.gz"
        (builder / name).write_bytes(gzip.compress(mbr + payload) + b"firmware metadata")
        (builder / "Makefile").write_text(
            f"info:\n\t@echo '{device}:'\nimage:\n"
            f"\t@test '$(PROFILE)' = '{device}'\n\t@test '$(FILES)' = '{self.files}'\n"
            f"\t@cp {name} '$(BIN_DIR)/'\n\t@echo 'dropbear - example' > '$(BIN_DIR)/fixture.manifest'\n"
        )
        return builder, target, device, release

    @unittest.skipUnless(shutil.which("mksquashfs") and shutil.which("unsquashfs"), "requires squashfs-tools")
    def test_fixture_delivery_and_corruption(self):
        args = self.make_fixture()
        output = self.work / "dist"
        build.assemble(*args, self.files, self.work, output)
        for line in (output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(digest, build.sha256(output / name))
        paths = [self.files / "etc/dropbear/authorized_keys", self.files / "etc/dropbear/dropbear_ed25519_host_key"]
        paths[0].chmod(0o644)
        for attempt, message in enumerate(("permission", "content")):
            with self.subTest(message=message):
                work = self.work / str(attempt)
                work.mkdir()
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    build.assemble(*args, self.files, work, work / "out")
                self.assertFalse((work / "out").exists())
            paths[0].chmod(0o600)
            paths[1].write_bytes(b"changed")

    @unittest.skipUnless(shutil.which("mksquashfs") and shutil.which("unsquashfs")
                         and shutil.which("zstd"), "requires squashfs-tools and zstd")
    def test_offline_archive_to_artifacts(self):
        builder, target, device, release = self.make_fixture()
        # A new build has its own private overlay path.
        makefile = builder / "Makefile"
        makefile.write_text(makefile.read_text().replace(
            f"test '$(FILES)' = '{self.files}'", "test -f '$(FILES)/etc/dropbear/authorized_keys'"
        ))
        archive = self.work / "fixture.tar.zst"
        subprocess.run(["tar", "--zstd", "-cf", archive, "-C", self.work, builder.name], check=True)
        (self.work / "dependencies.lock").write_text(
            f'release = "{release}"\n[targets."{target}"]\nimagebuilder_sha256 = "{build.sha256(archive)}"\n'
        )
        out = self.work / "delivery"
        args = ["build.sh", "--env", str(ROOT / ".env.example"), "--output", str(out)]
        with (patch.object(build, "ROOT", self.work),
              patch.object(build.platform, "system", return_value="Linux"),
              patch.object(build.platform, "machine", return_value="x86_64"),
              patch.object(build, "urlopen", return_value=archive.open("rb")) as download,
              patch.object(sys, "argv", args)):
            build.main()
            self.assertIn(f"/{release}/targets/{target}/", download.call_args.args[0])
            with self.assertRaisesRegex(ValueError, "new output"):
                build.main()
            self.assertEqual(download.call_count, 1)
        self.assertEqual(len(list(out.iterdir())), 3)

    def test_unavailable_device(self):
        (self.work / "Makefile").write_text("info:\n\t@echo another_device:\n")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            build.assemble(self.work, "rockchip/armv8", "some_device", "25.12.6",
                           self.files, self.work, self.work / "out")
