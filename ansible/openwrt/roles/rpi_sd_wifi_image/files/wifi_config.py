#!/usr/bin/env python3
"""Parse the SD configuration as data; never echo credential values."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata

COUNTRIES = frozenset("""AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP
KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ
VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split())


class ConfigError(ValueError):
    def __init__(self, reason, line=0):
        self.reason, self.line = reason, line
        super().__init__(f"{reason}:line={line}")


def parse_wifi_config(data):
    if len(data) > 4096:
        raise ConfigError("config_too_large")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise ConfigError("config_invalid_utf8") from None
    result = {}
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if any(unicodedata.category(c).startswith("C") for c in line):
            raise ConfigError("config_control_character", number)
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in {"country", "ssid", "password"}:
            raise ConfigError("config_unknown_key", number)
        if key in result:
            raise ConfigError("config_duplicate_key", number)
        if not value:
            raise ConfigError("config_empty_value", number)
        if key == "country" and value not in COUNTRIES:
            raise ConfigError("config_invalid_country", number)
        if key == "ssid" and not 1 <= len(value.encode("utf-8")) <= 32:
            raise ConfigError("config_invalid_ssid", number)
        if key == "password" and not (
            8 <= len(value) <= 63 and all(32 <= ord(c) <= 126 for c in value)
        ):
            raise ConfigError("config_invalid_password", number)
        result[key] = value
    if result.keys() != {"country", "ssid", "password"}:
        raise ConfigError("config_missing_key")
    return result


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sd-netboot-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as output:
            output.write(content.encode() if isinstance(content, str) else content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def supplicant_config(config):
    ssid = config["ssid"].encode()
    psk = hashlib.pbkdf2_hmac("sha1", config["password"].encode(), ssid, 4096, 32)
    return (
        f"country={config['country']}\nctrl_interface=/run/sd-netboot-state/wpa\n"
        "update_config=0\nnetwork={\n    scan_ssid=1\n    key_mgmt=WPA-PSK\n"
        "    proto=RSN\n    pairwise=CCMP\n"
        f"    ssid={ssid.hex()}\n    psk={psk.hex()}\n}}\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--supplicant-output", type=Path)
    args = parser.parse_args()
    try:
        with args.config.open("rb") as stream:
            config = parse_wifi_config(stream.read(4097))
        if args.supplicant_output:
            atomic_write(args.supplicant_output, supplicant_config(config))
    except ConfigError as error:
        print(json.dumps({"ok": False, "reason": error.reason, "line": error.line}))
        return 2
    except OSError:
        print(json.dumps({"ok": False, "reason": "config_io_error", "line": 0}))
        return 2
    print(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
