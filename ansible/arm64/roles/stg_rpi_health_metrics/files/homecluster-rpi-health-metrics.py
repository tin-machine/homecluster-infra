#!/usr/bin/env python3
"""Write host health gauges for node-exporter's textfile collector."""

from pathlib import Path
import os
import re
import subprocess
import tempfile
import sys
import time


OUTPUT = Path('/run/homecluster-node-exporter/rpi_health.prom')
ISCSI_CLASS = Path('/sys/class/iscsi_session')
THROTTLE_BITS = {
    'undervoltage': (0, 16),
    'frequency_capped': (1, 17),
    'throttled': (2, 18),
    'soft_temperature_limit': (3, 19),
}


def parse_throttled(output):
    match = re.fullmatch(r'throttled=0x([0-9a-fA-F]+)\s*', output)
    if match is None:
        raise ValueError('unexpected vcgencmd get_throttled output')
    return int(match.group(1), 16)


def render_metrics(throttled_output, iscsi_sessions):
    flags = parse_throttled(throttled_output)
    lines = [
        '# HELP homecluster_iscsi_sessions Active host iSCSI sessions.',
        '# TYPE homecluster_iscsi_sessions gauge',
        f'homecluster_iscsi_sessions {iscsi_sessions}',
        '# HELP homecluster_rpi_throttle_state Raspberry Pi current or since boot firmware condition.',
        '# TYPE homecluster_rpi_throttle_state gauge',
    ]
    for condition, (current_bit, historical_bit) in THROTTLE_BITS.items():
        for state, bit in (('current', current_bit), ('since_boot', historical_bit)):
            lines.append(
                f'homecluster_rpi_throttle_state{{condition="{condition}",state="{state}"}} '
                f'{int(bool(flags & (1 << bit)))}'
            )
    lines.extend([
        '# HELP homecluster_rpi_health_collection_success Host metric collection succeeded.',
        '# TYPE homecluster_rpi_health_collection_success gauge',
        'homecluster_rpi_health_collection_success 1',
    ])
    return '\n'.join(lines) + '\n'


def collect(vcgencmd='/usr/bin/vcgencmd', iscsi_class=ISCSI_CLASS):
    result = subprocess.run(
        [vcgencmd, 'get_throttled'],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    sessions = sum(path.is_dir() for path in iscsi_class.glob('session*'))
    return render_metrics(result.stdout, sessions)


def write_atomic(output, metrics):
    output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(metrics)
    try:
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def run(output=OUTPUT, vcgencmd='/usr/bin/vcgencmd', iscsi_class=ISCSI_CLASS):
    try:
        metrics = collect(vcgencmd=vcgencmd, iscsi_class=iscsi_class)
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        metrics = (
            '# HELP homecluster_rpi_health_collection_success Host metric collection succeeded.\n'
            '# TYPE homecluster_rpi_health_collection_success gauge\n'
            'homecluster_rpi_health_collection_success 0\n'
        )
        write_atomic(output, with_timestamp(metrics))
        return False
    write_atomic(output, with_timestamp(metrics))
    return True


def with_timestamp(metrics):
    return metrics + (
        '# HELP homecluster_rpi_health_collected_at_seconds Unix time of the last collection attempt.\n'
        '# TYPE homecluster_rpi_health_collected_at_seconds gauge\n'
        f'homecluster_rpi_health_collected_at_seconds {int(time.time())}\n'
    )


if __name__ == '__main__':
    sys.exit(0 if run() else 1)
