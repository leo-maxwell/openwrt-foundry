# First build on Fedora x86-64

Run from the repository root as an ordinary user. Use the public test configuration for the first build. It embeds the example SSH public key and device host key. The production routing configuration and RAM overlay require separate implementation and hardware testing.

## Local preflight

```sh
./build.sh --check --profile stable --env .env.example
python3 -B -m unittest discover -s tests -v
```

Preflight parses the deployment once, resolves the target lock, and renders the overlay in a private temporary directory. The fixture tests exercise real SquashFS extraction when `mksquashfs` and `unsquashfs` are on PATH. The archive test also needs `zstd`, `tar`, and `make`.

## Fedora build

Use Python 3.11 or later and an x86-64 Linux host with outbound HTTPS access and several GB free in `/tmp`. Install the build tools before running as an ordinary user:

```sh
sudo dnf install gcc gcc-c++ make binutils patch diffutils findutils file which \
  perl-core gawk grep sed git rsync unzip tar gzip bzip2 xz zstd wget curl \
  python3 openssh-clients squashfs-tools ncurses-devel zlib-devel
./build.sh --profile stable --env .env.example
```

The build downloads the ImageBuilder named in [dependencies.lock](../dependencies.lock), verifies its SHA-256, and checks the device against `make info`. ImageBuilder also runs its own prerequisite checks and resolves its default package set. Keep its package manifest with the image. The current lock pins the builder archive; an independent package-feed snapshot is still needed to guarantee identical package inputs across future rebuilds.

The first target is `rockchip/armv8`. Verification supports its MBR-partitioned SquashFS sysupgrade disk images. A new target needs a verified archive checksum and a verifier compatible with its image layout.

## Output

A successful reference build publishes three files under `dist/stable-friendlyarm_nanopi-r4s-25.12.6/`: the compressed sysupgrade image, the generated package manifest, and `SHA256SUMS`. From that directory, run `sha256sum -c SHA256SUMS`. Use `--output PATH` to choose a fresh destination for another run. The builder verifies embedded release, target, file bytes, and Unix permissions before publishing. The overlay, extracted root, and ImageBuilder workspace are temporary and private.
