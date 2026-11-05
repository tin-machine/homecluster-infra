"""Reviewed normal messages only. Unknown messages and explicit warnings survive."""
import ipaddress
import re

RULE_VERSION = '2026-11-05.2'
BAD_LEVELS = {'warn', 'warning', 'error', 'critical', 'alert', 'emerg'}
PREFIX = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[[^\]\r\n]+\] ')
KERNEL = re.compile(r'^<([0-7])>\[\s*[0-9]+\.[0-9]+\] ')


def syslog_normal_patterns(ssh_source=''):
    event = (r'^qlogd\[[0-9]+\]: event log: Users: System, Source IP: 127\.0\.0\.1, '
             r'Computer name: localhost, Content: ')
    end = r'\x00?$'
    patterns = [
        ('smart_started', event + r'\[Disk S\.M\.A\.R\.T\.\] Host: Disk [1-4] (Rapid|Complete) Test started\.' + end),
        ('smart_success', event + r'\[Disk S\.M\.A\.R\.T\.\] Host: Disk [1-4] (Rapid|Complete) Test result: Completed without error\.' + end),
        ('malware_scan', event + r'\[Malware Remover\] (Started scanning|Scan completed)\.' + end),
    ]
    if ssh_source:
        ip = str(ipaddress.IPv4Address(ssh_source))
        patterns.append(('collector_login', r'^qlogd\[[0-9]+\]: conn log: Users: admin, Source IP: '
                         + re.escape(ip) + r', Computer name: ---, Connection type: SSH, Accessed resources: ---, Action: (Login OK|Logout)' + end))
    return patterns


TEXT_RULES = [
    ('storage', 'raid_healthy', r'md_get_status: /dev/md[0-9]+ : status=0, progress=100\.000000\.'),
    ('storage', 'disk_conf_ok', r'NAS_Disk_Check_Conf: ret = 0, wwnP = \(null\)\.'),
    ('storage', 'disk_conf_call', r'NAS_Disk_Check_Conf: got call with \(0x[1-4], /mnt/HDA_ROOT/\.conf\)\.'),
    ('storage', 'disk_conf_match', r'NAS_Disk_Check_Conf: HAL disk WWN\((0x[1-4])\): [0-9A-Fa-f]{16} match conf WWN, the last dev_id\(\1\)!'),
    ('storage', 'volume_enumeration', r'Do_Get_Volume_Func: index = [0-9]+, count=[0-9]+\.'),
    ('hal', 'device_lookup', r'PD_Get_Sys_Name: Retrieve pd_sys_name value from /etc/enclosure_[0-9]+\.conf!'),
    ('sdmd', 'device_enumeration', r'SDM: (total enc = [0-9]+|scan [0-9]+th enc, total enc = [0-9]+|enc = [0-9]+ is (not )?internal\.|start predict enc = [0-9]+ port = [0-9]+ device)'),
    ('sdmd', 'health_prediction_started', r'SDM_Health_Predict:Start to predict sata disk'),
    ('kernel', 'raid_selftest_ok', r'raid6test: (test_disks\([0-9]+, [0-9]+\): faila= *[0-9]+\([DPQ]\) +failb= *[0-9]+\([DPQ]\) +OK|complete \([0-9]+ tests, 0 failures\))'),
]
COMPILED = [(kind, name, re.compile(pattern)) for kind, name, pattern in TEXT_RULES]
SMART = re.compile(r'id = ([0-9]+), name = ([A-Za-z0-9_-]+), status_flag = 0x[0-9a-f]+, value = ([0-9]+),worst = ([0-9]+), thresh = ([0-9]+),raw = ([0-9]+)')
ZERO_ATTRIBUTES = {
    5: 'Retired_Block_Count', 10: 'Spin_Retry_Count', 183: 'Runtime_Bad_Block',
    184: 'End-to-End_Error', 187: 'Reported_Uncorrect_Error', 188: 'Command_Timeout',
    189: 'High_Fly_Writes', 191: 'G-Sense_Error_Rate', 197: 'Current_Pending_Sector',
    198: 'Uncorrectable_Sector_Count', 199: 'SATA_R-Error_Count',
}
COUNTER_ATTRIBUTES = {
    1: 'Raw_Read_Error_Rate', 3: 'Spin_Up_Time', 4: 'Start_Stop_Count',
    7: 'Seek_Error_Rate', 9: 'Power-On_Hours', 12: 'Power_Cycle_Count',
    192: 'Power-Off_Retract_Count', 193: 'Load_Cycle_Count', 195: 'ECC_On-The-Fly_Error',
    240: 'Head_Flying_Hours', 241: 'Total_LBA_Written', 242: 'Total_LBA_Read',
}
READ_COMMANDS = [
    '/sbin/lvs vg1/tp1 -o lv_attr --noheadings 2>>/dev/null | /bin/sed s/^[[:space:]]*// 2>>/dev/null',
    "/bin/df -k /share/CACHEDEV1_DATA 2>>/dev/null | /usr/bin/tail -n1 | /bin/awk -F ' ' '{print $(NF-2)}'",
    '/sbin/lvs /dev/mapper/vg1-tp1 --unit S -o chunk_size --noheadings 2>>/dev/null | /bin/sed s/^[[:space:]]*// 2>>/dev/null',
]
NEW_READ_COMMANDS = [
    "/bin/df -k /share/CACHEDEV1_DATA 2>>/dev/null | /usr/bin/tail -n1 | /bin/awk -F ' ' '{print $(NF-3)}'",
    "/sbin/vgs vg1 --unit S -o vg_size --noheadings 2>>/dev/null | /bin/sed s/^[[:space:]]*// 2>>/dev/null",
    "/sbin/vgs vg1 --unit S -o vg_free --noheadings 2>>/dev/null | /bin/sed s/^[[:space:]]*// 2>>/dev/null",
    "/sbin/lvs /dev/mapper/vg1-tp1 --unit S -o lv_size --noheadings 2>>/dev/null | /bin/sed s/^[[:space:]]*// 2>>/dev/null",
    "/sbin/lvs /dev/mapper/vg1-tp1 -o used_data_blocks --noheadings  2>>/dev/null | /bin/sed s/^[[:space:]]*//  2>>/dev/null",
    "/sbin/dmsetup info --noheadings --columns -o minor vg1-tp1-tpool 2>>/dev/null",
    "/sbin/vgs vg1 -o vg_extent_size --noheadings 2>>/dev/null  | /bin/sed s/^[[:space:]]*//  2>>/dev/null",
    "/sbin/lvs -a /dev/mapper/vg1-tp1_tmeta -o lv_size --unit S 2>>/dev/null | /bin/awk 'NR==2 {print $1}' 2>>/dev/null",
]
READ_SUCCESS = {f'Perform cmd "{cmd}" OK, cmd_rsp=0, reason code:0.'
                for cmd in READ_COMMANDS + NEW_READ_COMMANDS}

ENUM = re.compile(
    r'Volume_Enumerate_(General|Data)_Volumes: got called with \(0x[0-9A-Fa-f]+, 772\)\.')
HD = re.compile(r'SystemHdTableEx_handler: hdindex=[1-4], model=ST8000DM004-2CX188\.')
BLK = re.compile(
    r'Blk_Dev_Generate_Mount_Point: mount point for "/dev/mapper/cachedev1" is "/share/CACHEDEV1_DATA", is_internal is 1\.')
POOLSIZE = re.compile(
    r'Pool_TP_Get_Size: \[Pool\(1\)\] \*lldatalP: ([0-9]+), \*llfreeP: ([0-9]+), \*llmetaP: ([0-9]+), \*llmeta_spareP: ([0-9]+)')
POOLFULL = re.compile(
    r'Pool_Is_Full: \[Pool\(1\)\] full: 0, \*thin_lv_exist: 0, \*snapshot_exist: 0, free_size: ([0-9]+), limit: ([0-9]+)\.')
LED = re.compile(
    r'GPIO_Set_NAS_Status_LED: event = 0x(?:1000|2000),status = 0, value = 0x0, formatting_count = 0')


def normal_pattern(kind, message, level='unknown', ssh_source=''):
    if level in BAD_LEVELS:
        return None
    if kind == 'syslog':
        if level != 'info':
            return None
        return next((name for name, regex in syslog_normal_patterns(ssh_source)
                     if re.fullmatch(regex, message)), None)
    body = PREFIX.sub('', message)
    if kind == 'kernel':
        match = KERNEL.match(body)
        if match:
            if int(match[1]) <= 4:
                return None
            body = body[match.end():]
    for source, name, regex in COMPILED:
        if source == kind and regex.fullmatch(body):
            return name
    if kind == 'storage':
        if body in READ_SUCCESS:
            return 'storage_read_success'
        if ENUM.fullmatch(body):
            return 'storage_volume_enumerate'
        if HD.fullmatch(body):
            return 'storage_system_hd_table_ex'
        if BLK.fullmatch(body):
            return 'storage_blk_dev_mount'
        if (m := POOLSIZE.fullmatch(body)):
            total, free, meta, spare = map(int, m.groups())
            if total > 0 and 0 < free <= total and meta > 0 and spare == 0:
                return 'storage_pool_tp_get_size'
            return None
        if (m := POOLFULL.fullmatch(body)):
            free, limit = map(int, m.groups())
            if limit > 0 and free > limit:
                return 'storage_pool_is_full'
            return None
    if kind == 'hal':
        if LED.fullmatch(body):
            return 'hal_gpio_led'
        match = SMART.fullmatch(body)
        if match:
            ident, name, value, worst, threshold, raw = match.groups()
            ident, value, worst, threshold, raw = map(int, (ident, value, worst, threshold, raw))
            if value <= threshold or (threshold > 0 and worst <= threshold):
                return None
            if ZERO_ATTRIBUTES.get(ident) == name and raw == 0:
                return 'smart_counter_zero'
            if COUNTER_ATTRIBUTES.get(ident) == name:
                return 'smart_attribute_healthy'
            if (ident, name) in {(190, 'Airflow_Temperature_Cel'), (194, 'Temperature_Celsius')} and 1 <= (raw & 255) <= 50:
                return 'smart_temperature_normal'
    return None
