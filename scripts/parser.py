"""Validate a deployment and optionally render declared files into a new directory."""

import argparse
import base64
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
import tomllib
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROFILES = Path(__file__).resolve().parents[1] / "profiles"
NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def load_profile(name, parents=()):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in parents:
        raise ValueError("invalid profile name or inheritance cycle")
    with (PROFILES / f"{name}.toml").open("rb") as stream:
        profile = tomllib.load(stream)
    if not isinstance(profile, dict) or profile.keys() - {"extends", "inputs", "files", "checks"}:
        raise ValueError("invalid profile structure")
    result = {"inputs": {}, "files": {}, "checks": []}
    if "extends" in profile:
        result = load_profile(profile["extends"], (*parents, name))
    for key, rule in profile.get("inputs", {}).items():
        if not NAME.fullmatch(key) or not isinstance(rule, dict):
            raise ValueError(f"invalid input declaration: {key}")
        allowed = {"type", "required", "when", "min", "max", "min_items", "choices", "pattern"}
        if "type" not in rule or rule.keys() - allowed:
            raise ValueError(f"{key}: missing type or unknown rule field")
    for section in ("inputs", "files"):
        result[section].update(profile.get(section, {}))
    result["checks"].extend(profile.get("checks", []))
    return result


def read_env(path, inputs):
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not NAME.fullmatch(key):
            raise ValueError(f"invalid assignment on line {number}")
        if key not in inputs or key in values:
            raise ValueError(f"{key}: unknown or duplicate input on line {number}")
        values[key] = value
    return values


def convert(raw, spec):
    kind = spec["type"]
    if kind == "integer":
        if not re.fullmatch(r"-?[0-9]+", raw):
            raise ValueError
        value = int(raw)
        if value < spec.get("min", value) or value > spec.get("max", value):
            raise ValueError
        return value
    if kind == "boolean":
        return {"true": True, "false": False}[raw]
    if kind in ("list", "ip_list"):
        items = json.loads(raw)
        if not isinstance(items, list) or len(items) < spec.get("min_items", 0):
            raise ValueError
        for item in items:
            if not isinstance(item, str) or not item or any(c in item for c in "\r\n\0"):
                raise ValueError
            if kind == "ip_list":
                ipaddress.ip_address(item)
        return items
    if kind == "base64":
        return base64.b64decode(raw, validate=True)
    if kind == "ipv4_interface":
        if "/" not in raw:
            raise ValueError
        return ipaddress.IPv4Interface(raw)
    if kind == "uuid":
        return str(UUID(raw))
    if kind == "timezone":
        ZoneInfo(raw)
    elif kind == "time":
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", raw):
            raise ValueError
    elif kind != "string":
        raise ValueError
    if "choices" in spec and raw not in spec["choices"]:
        raise ValueError
    if "pattern" in spec and not re.fullmatch(spec["pattern"], raw):
        raise ValueError
    return raw


def parse_inputs(profile, path):
    inputs = profile["inputs"]
    raw = read_env(path, inputs)
    values = {}

    def resolve(key, visiting):
        if key in values:
            return values[key]
        if key not in inputs or key in visiting:
            raise ValueError("unknown or circular profile condition")
        spec = inputs[key]
        conditions = spec.get("when", {})
        enabled = all(resolve(other, visiting | {key}) == expected
                      for other, expected in conditions.items())
        value = None
        if enabled:
            entry = raw.get(key, "")
            if not entry and spec.get("required", True):
                raise ValueError(f"{key}: required input is empty or missing")
            if entry:
                try:
                    value = convert(entry, spec)
                except (ValueError, KeyError, ZoneInfoNotFoundError):
                    raise ValueError(f"{key}: invalid {spec['type']} value") from None
        values[key] = value
        return value

    for key in inputs:
        resolve(key, set())
    for check in profile["checks"]:
        if check == "dropbear_ed25519_key":
            blob = values["SSH_HOST_PRIVATE_KEY_B64"]
            header = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x40"
            if not blob.startswith(header) or len(blob) != len(header) + 64:
                raise ValueError("SSH_HOST_PRIVATE_KEY_B64: expected a Dropbear Ed25519 key")
            continue
        if check != "lan_dhcp_pool":
            raise ValueError(f"unknown profile check: {check}")
        lan = values["LAN_IPV4_CIDR"]
        first = values["LAN_DHCP_START"]
        last = first + values["LAN_DHCP_LIMIT"] - 1
        router = int(lan.ip) - int(lan.network.network_address)
        broadcast = lan.network.num_addresses - 1
        if not 0 < router < broadcast or last >= broadcast or first <= router <= last:
            raise ValueError("LAN_IPV4_CIDR: invalid router address or DHCP pool")
    return values


def render_files(files, values, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("output directory already exists")
    with TemporaryDirectory(dir=destination.parent) as work:
        root = Path(work) / "files"
        root.mkdir(mode=0o700)
        for target, rule in files.items():
            path = PurePosixPath(target)
            if not path.is_absolute() or str(path) != target or path.root != "/" or ".." in path.parts:
                raise ValueError("invalid profile output path")
            sources = rule.keys() - {"mode"}
            if len(sources) != 1:
                raise ValueError(f"{target}: expected one source")
            kind = next(iter(sources))
            value = values[rule[kind]]
            if kind == "text_from" and isinstance(value, str):
                data = (value + "\n").encode()
            elif kind == "lines_from" and isinstance(value, list):
                data = "".join(line + "\n" for line in value).encode()
            elif kind == "bytes_from" and isinstance(value, bytes):
                data = value
            else:
                raise ValueError(f"{target}: incompatible file source")
            mode = rule.get("mode", "0600")
            if not isinstance(mode, str) or not re.fullmatch(r"0[0-7]{3}", mode):
                raise ValueError(f"{target}: expected quoted octal mode")
            output = root.joinpath(*path.parts[1:])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            output.chmod(int(mode, 8))
        root.rename(destination)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--profile", default="stable")
    cli.add_argument("--env", type=Path, default=Path(".env"))
    cli.add_argument("--output", type=Path, help="create a private files directory")
    args = cli.parse_args()
    try:
        profile = load_profile(args.profile)
        values = parse_inputs(profile, args.env)
        if args.output:
            render_files(profile["files"], values, args.output)
    except tomllib.TOMLDecodeError:
        cli.exit(1, "Invalid TOML profile\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        cli.exit(1, f"{error}\n")
    print(f"Validated {len(values)} inputs for {args.profile}.")


if __name__ == "__main__":
    main()
