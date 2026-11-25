# OpenWrt Foundry

Build reproducible, immutable OpenWrt firmware from a public profile and one private deployment file. The [stable profile](PROFILE_STABLE.md) defines production routing behavior and hardware acceptance tests. The NanoPi R4S is the reference deployment.

## Configuration

- **Profile authors** declare inputs, validation rules, and output files in `profiles/<name>.toml` using TOML 1.0. An input has a type and may specify constraints or a `when` condition. Use named checks for relationships such as a DHCP pool fitting within its subnet.
- **Deployment owners** supply settings and credentials together in a private `.env`. Use one uppercase `NAME=value` per line. Split at the first equals sign and preserve the rest literally, including spaces, quotes, and dollar signs. Put comments on separate lines and write lists as single-line JSON arrays. The public [.env.example](../.env.example) contains test values.
- A child profile can declare `extends = "stable"` and add inputs or file rules. A declaration with an inherited name or output path replaces that entire rule. Checks run in parent-to-child order. Adding an input that uses existing types and renderers requires only a profile change and a deployment value.

For example, this child profile adds one SSH login banner. Set `LOGIN_BANNER=Lab router` in its deployment file.

```toml
extends = "stable"

[inputs]
LOGIN_BANNER = { type = "string" }

[files."/etc/banner"]
text_from = "LOGIN_BANNER"
mode = "0644"
```

## Build pipeline

1. **Validate.** Use Python 3.11 or later and its standard-library TOML reader. Run `python3 scripts/parser.py --profile stable --env .env.example` to check the public example. Reject duplicate or undeclared deployment keys, missing required values, and failed constraints. Report errors without printing credentials.
2. **Render.** Add `--output <new-directory>` to render declared files. Paths inside this private directory match paths on the router. File rules write text, one list entry per line, or decoded bytes and apply the declared permissions. OpenWrt configuration generation must preserve literal values when encoding UCI. Keep the source `.env` outside the firmware.
3. **Assemble.** The build entry point is `./build.sh --profile <name>` on a replaceable x86-64 Linux host. Select the latest stable OpenWrt release when updating the lockfile. Use the deployment’s `OPENWRT_TARGET` (target/subtarget) to select the ImageBuilder and verify that its profile list contains `OPENWRT_DEVICE_PROFILE`. Pass that ID as ImageBuilder’s `PROFILE`. Pin the hardware selection, release, tool and package versions, and checksums for subsequent builds. Pass the rendered directory to the official ImageBuilder as `FILES` and embed it in SquashFS. Use the full OpenWrt build system when a boot component requires rebuilding.
4. **Verify and release.** Unpack the image and check its target, filesystem, generated files, and permissions. Reproduce the image and configuration on a clean builder using the same source revision, locked dependencies, and deployment file. Tag releases and produce a flashable image, package manifest, and SHA-256 checksum.

See the [Fedora build instructions](BUILD.md) for local preflight, tool requirements, and test image verification.

## Production builds

A private GitHub repository builds a reviewed commit from the public repository. Store the complete deployment file in one `FIRMWARE_ENV` GitHub Secret and materialize it in a private temporary directory on the runner. A VPS reads the same file locally. Restrict the file to its owner (mode `0600`), keep a recoverable encrypted backup, and treat the rendered directory and production artifacts as private.
