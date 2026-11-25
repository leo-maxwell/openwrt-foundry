# Immutable OpenWrt for NanoPi R4S

Build a router that restores its declared configuration and resumes routing automatically after power loss. The target is 120 days without an unplanned service interruption. Scheduled reboots are acceptable.

Target the latest stable OpenWrt release for the NanoPi R4S (RK3399, `rockchip/armv8`). Confirm release, device profile, and ImageBuilder availability before implementation.

## Runtime and configuration

- Boot from microSD with a read-only SquashFS root and a tmpfs overlay. Keep the card read-only throughout startup and operation. Disable writable `rootfs_data`, extroot, and swap. Rebooting discards temporary LuCI changes and restores the configuration baked into the image.
- The production `stable` profile provides routing, PPPoE, NAT, firewall, DHCP, DNS, IPv6, NTP, SSH, and LuCI. Retain native `procd`, `netifd`, and the hardware watchdog. The stable profile enables IPv4 masquerading on WAN, permits LAN-to-WAN forwarding, and blocks unsolicited WAN access except for explicitly allowed traffic. Disable automatic updates. The initial stable profile leaves flow offloading disabled pending performance and stability testing. Reserve optional services for the separate `plugins` profile.
- Generate UCI and related files from a base YAML configuration plus a profile. Keep unknown deployment values as placeholders.
- Request an IPv6 prefix from the ISP via DHCPv6-PD. On the R4S LAN, advertise the default route and a /64 for SLAAC. Offer stateful DHCPv6 address assignment (IA_NA) on the same link.
- Delegate a distinct, nonoverlapping prefix to each requesting downstream router via DHCPv6-PD (IA_PD). Route each prefix through the router that received it, so that router can manage its own subnets and its clients have native IPv6 connectivity without NAT66. Update advertisements, delegations, and routes when the ISP prefix changes. If there is too little space for a grant, preserve the R4S LAN /64 and report the shortage. Resume delegation when space becomes available.
- Support optional daily or weekly reboots at a configured local time and weekday where applicable. An incorrect clock must never cause a reboot loop.

## Firmware inputs

Resolve settings from base and profile YAML. Validate required values before building a production image. Times use quoted 24-hour local time from `"00:00"` through `"23:59"`.

### System and access

- `SYSTEM_HOSTNAME` — Router hostname as a single DNS label.
- `SYSTEM_TIMEZONE` — IANA time zone name used for schedules and log dates.
- `SYSTEM_LANGUAGE` — LuCI language code supported by the image.
- `SYSTEM_NTP_SERVERS` — Nonempty list of NTP server hostnames or IP addresses.
- `SSH_AUTHORIZED_KEYS` — List of administrator public keys, one OpenSSH public key per entry.

### LAN and WAN

- `LAN_IPV4_CIDR` — Router address with its IPv4 prefix length. The address must be usable in the subnet.
- `LAN_DHCP_START` — Integer offset from the subnet’s network address to the first lease address.
- `LAN_DHCP_LIMIT` — Positive integer lease count. The entire pool must fit within the usable subnet and exclude the router address.
- `WAN_INTERFACE` — Physical OpenWrt interface name for PPPoE, before VLAN tagging.
- `WAN_VLAN_ID` — Integer from 1 through 4094. Omit for untagged PPPoE.

### DNS and IPv6

- `DNS_SERVERS` — Optional list of upstream resolver IP addresses. Omit or leave empty to use ISP-provided DNS. A nonempty list supplies all upstream resolvers.
- `IPV6_WAN_PD_REQUEST_LENGTH` — Optional integer prefix-length hint in bits (1–64). Omit to request delegation without a length preference. The ISP determines the actual allocation.
- `IPV6_DOWNSTREAM_PD_LENGTH` — Preferred prefix length in bits (1–64) for each downstream router. Each grant must fit within the ISP allocation and leave a distinct /64 for the LAN.

### USB storage and logging

- `USB_UUID` — Filesystem UUID of the ext4 log volume.
- `USB_MOUNT_OPTIONS` — Comma-separated ext4 mount options. The base profile uses `noatime,errors=remount-ro`.
- `LOG_ROTATION_TIME` — Local time to open the next daily log and archive the previous one.
- `LOG_LEVEL` — Least severe priority to retain. Allowed values, from highest to lowest severity, are `emerg`, `alert`, `crit`, `err`, `warning`, `notice`, `info`, and `debug`.
- `LOG_RESERVE_BYTES` — Minimum free space on the log volume, in whole bytes. Defaults to 1000000000 (1 GB) and may only be increased.

### Scheduled reboot

- `REBOOT_ENABLED` — YAML boolean. With `false`, ignore all other reboot settings. With `true`, require a frequency and time.
- `REBOOT_FREQUENCY` — Either `daily` or `weekly` when scheduling is enabled.
- `REBOOT_TIME` — Local time to reboot on each scheduled day.
- `REBOOT_DAY_OF_WEEK` — Required for an enabled weekly schedule. Accepts `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, or `sun`. Daily schedules ignore this value.

### Private credentials

Supply these inputs through GitHub Secrets or identically named private files on the VPS. Keep their values outside version control.

- `PPPOE_USERNAME` — Account name exactly as supplied by the ISP.
- `PPPOE_PASSWORD` — Original ISP password, including any spaces or punctuation.
- `ADMIN_PASSWORD_HASH` — Complete OpenWrt-compatible shadow password hash for the administrator account.
- `SSH_HOST_PRIVATE_KEY` — Complete device host private key in a format accepted by the image’s SSH server. Preserve its encoding across builds.

## USB logging

Use native OpenWrt logging and a dedicated ext4 USB drive mounted by UUID. Assume at least 16 GB of capacity and up to 128 MB of logs per day. Apply `LOG_LEVEL` to USB output, including messages at the selected priority and all higher severities. Boot and routing must continue if the drive is missing, full, damaged, or stalled.

- **Name and rotate.** Write plain text to `full_log_YYYY.MM.DD.txt`. The date identifies the start of the logging period in the configured time zone. At `LOG_ROTATION_TIME` each day, direct new messages to the new period’s file before compressing the closed file. After a reboot within the same period, append to the existing file.
- **Compress and retire.** Use gzip for the daily text archive, named `full_log_YYYY.MM.DD.txt.gz`. Write to a temporary file on the USB drive, verify the completed archive, and publish it under its final name before deleting the plain-text source. An interrupted compression must leave the source available for retry.
- **Keep free space.** Logs may use the entire volume above the free-space reserve. Before appending or compressing, allow room for the write and the reserve, including temporary archive space. Delete completed archives oldest first when more room is needed or free space falls below the reserve. If eligible archives run out, pause USB writes until space recovers.

## Builds and credentials

The public repository owns the build scripts, templates, profiles, examples, and dependency lockfile. Build with `./build.sh --profile stable` or `--profile plugins` on a replaceable x86-64 Linux host. Use the official ImageBuilder and its `FILES` overlay to embed configuration in SquashFS. Use the full OpenWrt build system only if boot changes require it. Tag releases and produce a flashable image, package manifest, and SHA-256 checksum.

A separate private GitHub repository runs production builds against a reviewed public commit SHA. The workflow reads private inputs from GitHub Secrets. A VPS supplies the same inputs from a private directory outside the checkout. Keep recoverable copies independently of either builder. Keep production images and build artifacts private.

## Acceptance

- Verify the overlay, first-boot scripts, host key handling, and random initialization against the target release. Confirm zero microSD writes at boot and in steady state.
- Pass at least 50 abrupt power cuts, including under heavy traffic and during PPPoE dialing. Recover configuration and connectivity automatically without filesystem repair.
- Exercise USB removal, exhaustion, and I/O failure while routing. Verify IPv4, SSH, LuCI, PPPoE reconnection, and watchdog recovery.
- On the R4S LAN, verify SLAAC and stateful DHCPv6 address assignment with both services enabled. Behind a downstream router, verify that a client receives an address from the delegated prefix and reaches the IPv6 Internet using that address. Repeat after ISP renumbering. With only one ISP-provided /64, verify that the R4S LAN stays online, the shortage is reported, and downstream delegation recovers once sufficient space returns.
- Reproduce the firmware and configuration on a clean Linux host. Complete an initial 30-day stability run, then demonstrate the 120-day target.
