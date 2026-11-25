"""Build and verify an OpenWrt test image using the locked Linux ImageBuilder."""

import argparse
import filecmp
import hashlib
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib
from urllib.request import urlopen
import zlib

from parser import load_profile, parse_inputs, render_files

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack_builder(url, digest, work):
    archive = work / "builder.tar.zst"
    with urlopen(url, timeout=120) as response, archive.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    if sha256(archive) != digest:
        raise ValueError("ImageBuilder SHA-256 mismatch")
    builder = work / "builder"
    builder.mkdir()
    subprocess.run(["tar", "--zstd", "-xf", archive, "--strip-components=1", "-C", builder], check=True)
    return builder


def unpack_disk(image, disk):
    # Rockchip appends sysupgrade metadata after the first gzip member.
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    with image.open("rb") as source, disk.open("wb") as dest:
        while not decoder.eof:
            chunk = source.read(64 * 1024)
            if not chunk:
                raise ValueError("truncated gzip disk image")
            dest.write(decoder.decompress(chunk))


def verify_image(image, files, work, release, target):
    disk = work / "disk.img"
    unpack_disk(image, disk)
    # The locked Rockchip sysupgrade format uses 512-byte MBR sectors.
    offsets = []
    with disk.open("rb") as stream:
        mbr = stream.read(512)
        if mbr[510:] != b"\x55\xaa":
            raise ValueError("expected an MBR disk image")
        for pos in range(446, 510, 16):
            entry = mbr[pos:pos + 16]
            if entry[4] != 0x83:
                continue
            offset = int.from_bytes(entry[8:12], "little") * 512
            stream.seek(offset)
            if stream.read(4) == b"hsqs":
                offsets.append(offset)
    if len(offsets) != 1:
        raise ValueError("expected one SquashFS partition in the final image")
    extracted = work / "verified"
    expected = sorted(p.relative_to(files) for p in files.rglob("*") if p.is_file())
    if not expected:
        raise ValueError("profile must render files for image verification")
    release_file = Path("etc/openwrt_release")
    members = sorted(set(expected) | {release_file})
    subprocess.run(["unsquashfs", "-no-progress", "-no-wildcards", "-o", str(offsets[0]),
                    "-d", extracted, disk, *members], check=True)
    metadata = (extracted / release_file).read_text().splitlines()
    if f"DISTRIB_RELEASE='{release}'" not in metadata or f"DISTRIB_TARGET='{target}'" not in metadata:
        raise ValueError("embedded release or build target mismatch")
    for rel in expected:
        actual = extracted / rel
        source = files / rel
        mode = actual.lstat().st_mode
        if (not stat.S_ISREG(mode) or stat.S_IMODE(mode) != stat.S_IMODE(source.stat().st_mode)
                or not filecmp.cmp(source, actual, shallow=False)):
            raise ValueError(f"embedded file verification failed: {rel}")


def assemble(builder, target, device, release, files, work, output):
    info = subprocess.check_output(["make", "-s", "info"], cwd=builder, text=True)
    if f"{device}:" not in info.splitlines():
        raise ValueError("selected device is unavailable in the locked ImageBuilder")
    artifacts = work / "images"
    artifacts.mkdir()
    subprocess.run(["make", "image", f"PROFILE={device}", f"FILES={files}",
                    f"BIN_DIR={artifacts}"], cwd=builder, check=True)
    prefix = f"openwrt-{release}-{target.replace('/', '-')}"
    image = artifacts / f"{prefix}-{device}-squashfs-sysupgrade.img.gz"
    manifests = list(artifacts.glob("*.manifest"))
    if not image.is_file() or len(manifests) != 1 or not manifests[0].stat().st_size:
        raise ValueError("missing device SquashFS image or unambiguous package manifest")
    verify_image(image, files, work, release, target)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as staging:
        ready = Path(staging) / "release"
        ready.mkdir(mode=0o700)
        checksums = []
        for artifact in (image, manifests[0]):
            shutil.copyfile(artifact, ready / artifact.name)
            checksums.append(f"{sha256(ready / artifact.name)}  {artifact.name}\n")
        (ready / "SHA256SUMS").write_text("".join(checksums))
        ready.rename(output)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--env", type=Path, default=Path(".env"))
    cli.add_argument("--profile", default="stable")
    cli.add_argument("--output", type=Path, help="new private artifact directory")
    cli.add_argument("--check", action="store_true", help="validate and render locally without downloading or running ImageBuilder")
    args = cli.parse_args()
    profile = load_profile(args.profile)
    inputs = parse_inputs(profile, args.env)
    target = inputs["OPENWRT_TARGET"]
    device = inputs["OPENWRT_DEVICE_PROFILE"]
    lock = tomllib.loads((ROOT / "dependencies.lock").read_text())
    release = lock["release"]
    if not isinstance(device, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", device):
        raise ValueError("invalid OPENWRT_DEVICE_PROFILE")
    if target not in lock["targets"]:
        raise ValueError(f"no ImageBuilder lock for {target}")
    archive = f"openwrt-imagebuilder-{release}-{target.replace('/', '-')}.Linux-x86_64.tar.zst"
    url = f"https://downloads.openwrt.org/releases/{release}/targets/{target}/{archive}"
    output = args.output or ROOT / "dist" / f"{args.profile}-{device}-{release}"
    with TemporaryDirectory(prefix="openwrt-", dir="/tmp") as temp:
        work = Path(temp)
        files = work / "files"
        render_files(profile["files"], inputs, files)
        print(f"OpenWrt {release} | {target} | {device} | {len(profile['files'])} overlay files", flush=True)
        if args.check:
            return
        if (platform.system(), platform.machine()) != ("Linux", "x86_64"):
            raise ValueError("ImageBuilder requires Linux x86-64; use --check for local validation")
        if output.exists() or output.is_symlink():
            raise ValueError("choose a new output directory")
        for tool in ("make", "tar", "zstd", "unsquashfs"):
            if not shutil.which(tool):
                raise ValueError(f"missing build tool: {tool}")
        builder = unpack_builder(url, lock["targets"][target]["imagebuilder_sha256"], work)
        assemble(builder, target, device, release, files, work, output)
        print(f"Verified test image, manifest and SHA256SUMS in {output}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, zlib.error) as error:
        sys.exit(str(error))
