#!/usr/bin/env python3
"""Build an isolated Pi4 SD artifact. Never write block devices or the source root."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time

FILES = Path(__file__).resolve().parent
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
KERNEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}$')
EMPTY_WIFI = b'country=JP\nssid=\npassword=\n'


def command(argv, **kwargs):
    # A private build log is the detailed evidence; stdout is a compact receipt.
    with open(LOG, 'ab') as log:
        kwargs.setdefault('stdout',log)
        kwargs.setdefault('stderr',log)
        p = subprocess.run([str(x) for x in argv], **kwargs)
    if p.returncode:
        raise RuntimeError('build_command_failed:' + Path(str(argv[0])).name)


def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def tree_digest(directory):
    h=hashlib.sha256()
    for p in sorted(directory.rglob('*')):
        if p.is_file() and not p.is_symlink():
            h.update(p.relative_to(directory).as_posix().encode()+b'\0')
            h.update(bytes.fromhex(digest(p)))
    return h.hexdigest()


def mbr(image, size_mib, signature):
    if size_mib < 256 or size_mib > 4096:
        raise ValueError('image_size_out_of_bounds')
    size=size_mib*1024*1024
    with open(image,'xb') as f:
        f.truncate(size)
        data=bytearray(512)
        struct.pack_into('<I',data,440,signature)
        data[446:462]=struct.pack('<B3sB3sII',0x80,b'\xfe\xff\xff',0x0c,b'\xfe\xff\xff',2048,size//512-2048)
        data[510:512]=b'\x55\xaa'
        f.write(data)


def source_is_readonly(path):
    value=subprocess.check_output(['findmnt','-n','-o','OPTIONS','-T',str(path)],text=True).strip()
    if 'ro' not in value.split(','):
        raise ValueError('sysroot_must_be_mounted_readonly')


def assert_dracut_log_ok(data):
    if re.search(rb'dracut\[E\]|dracut-install: Failed',data):
        raise ValueError('dracut_reported_error_despite_exit_code')


def copy_plain(source, destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    if source.is_dir():
        shutil.copytree(source,destination,symlinks=False)
    elif source.is_file():
        shutil.copyfile(source,destination)
    else:
        raise ValueError('required_boot_asset_missing')


def newc_write(root, output):
    """Deterministic newc archive; no owner names or build-host absolute paths."""
    with open(output,'xb') as stream:
        def entry(name, mode, data, inode, rdevmajor=0, rdevminor=0):
            n=name.encode()+b'\0'
            fields=[inode,mode,0,0,1,0,len(data),0,0,rdevmajor,rdevminor,len(n),0]
            header=b'070701'+b''.join(f'{v:08x}'.encode() for v in fields)
            stream.write(header+n)
            stream.write(b'\0'*((-len(header)-len(n))%4))
            stream.write(data)
            stream.write(b'\0'*((-len(data))%4))
        for number,p in enumerate(sorted(root.rglob('*')),1):
            st=p.lstat()
            if stat.S_ISREG(st.st_mode):
                data=p.read_bytes()
            elif stat.S_ISLNK(st.st_mode):
                data=os.readlink(p).encode()
            elif stat.S_ISDIR(st.st_mode):
                data=b''
            elif (stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode) or stat.S_ISFIFO(st.st_mode)) and p.relative_to(root).parts[0]=='dev':
                data=b''
                entry(p.relative_to(root).as_posix(),st.st_mode,data,number,
                      os.major(st.st_rdev),os.minor(st.st_rdev))
                continue
            else:
                raise ValueError('unexpected_initramfs_file_type')
            entry(p.relative_to(root).as_posix(),st.st_mode,data,number)
        entry('TRAILER!!!',0,b'',0)
        stream.write(b'\0'*((-stream.tell())%512))


def mirror_userspace(root, destination):
    destination.mkdir(parents=True)
    for name in ['usr','bin','sbin','lib','lib64']:
        source=root/name
        if source.is_symlink():
            (destination/name).symlink_to(os.readlink(source))
        elif source.is_dir():
            shutil.copytree(source,destination/name,symlinks=True,
                            ignore=shutil.ignore_patterns('modules','firmware','sd-netboot'))
    etc=destination/'etc'
    etc.mkdir(exist_ok=True)
    (etc/'initrd-release').write_text('ID=sd-netboot-runtime\n')
    (etc/'passwd').write_text('root:x:0:0:root:/root:/bin/sh\nsystemd-network:x:192:192:Network:/run/systemd:/sbin/nologin\n')
    (etc/'group').write_text('root:x:0:\nsystemd-network:x:192:\n')
    (etc/'nsswitch.conf').write_text('passwd: files\ngroup: files\nhosts: files dns\n')
    (etc/'systemd/network').mkdir(parents=True)
    if (root/'etc/ld.so.cache').is_file():
        shutil.copy2(root/'etc/ld.so.cache',etc/'ld.so.cache')


def build_initrd(args, work, manifest):
    tree=work/'initrd'
    base=work/'base.cpio'
    stdlib=args.sysroot/'usr/lib/python3.14'
    if not stdlib.is_dir():
        raise ValueError('python314_sysroot_required')
    binaries=['/usr/bin/python3.14','/usr/bin/wpa_supplicant','/usr/bin/wpa_cli',
              '/usr/bin/iw','/usr/lib/systemd/systemd-networkd','/usr/bin/networkctl',
              '/usr/sbin/mount.nfs4','/usr/bin/cp','/usr/sbin/blkid']
    # dracut-install resolves all ELF dependencies, including Python extensions.
    extensions=['/'+str(p.relative_to(args.sysroot)) for p in (stdlib/'lib-dynload').glob('*.so')]
    module_dir=args.sysroot/'usr/lib/modules'/args.kernel_release
    drivers='brcmfmac brcmutil cfg80211 rfkill mmc_block sdhci sdhci_iproc sdhci_pltfm sdhci_brcmstb bcm2835 bcm2835_mmc vfat nls_cp437 nls_iso8859_1 overlay nfs nfsv4 sunrpc iscsi_tcp sd_mod ext4'
    for driver in drivers.split():
        command(['modinfo','--basedir',args.sysroot,'--set-version',args.kernel_release,'--field','filename',driver])
    # Explicit modules/empty configuration prevent host-only credentials, cmdline and root settings.
    empty=work/'empty-conf'
    empty.mkdir()
    command(['dracut','--force','--no-compress','--no-hostonly','--no-hostonly-cmdline',
             '--no-early-microcode','--conf','/dev/null','--confdir',empty,
             '--sysroot',args.sysroot,'--kmoddir',module_dir,'--kver',args.kernel_release,
             '--modules','base systemd systemd-initrd systemd-udevd systemd-modules-load fs-lib kernel-modules kernel-network-modules shutdown',
             '--add-drivers',drivers,'--install',' '.join(binaries+extensions),
             '--tmpdir',work,'--logfile',work/'dracut.log',base])
    assert_dracut_log_ok(Path(LOG).read_bytes())
    tree.mkdir()
    with open(base,'rb') as stream:
        command(['cpio','--extract','--make-directories','--no-absolute-filenames','--quiet'],stdin=stream,cwd=tree)
    for driver in drivers.split():
        command(['modinfo','--basedir',tree,'--set-version',args.kernel_release,'--field','filename',driver])
    # Never copy site config from the sysroot, including host login identities.
    for name in ['root','home','etc/ssh','etc/shadow','etc/gshadow','etc/machine-id','etc/fstab','etc/cmdline.d']:
        p=tree/name
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()
    (tree/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\n')
    (tree/'etc/group').write_text('root:x:0:\n')
    # Pure stdlib plus ELF dependencies already installed by dracut.
    shutil.copytree(stdlib,tree/'usr/lib/python3.14',dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('site-packages','test','tests','__pycache__','idlelib','tkinter','ensurepip'))
    for relative in ['usr/lib/firmware/brcm','usr/lib/firmware/regulatory.db','usr/lib/firmware/regulatory.db.p7s']:
        source=args.sysroot/relative
        if source.exists():
            destination=tree/relative
            if destination.is_dir():
                shutil.rmtree(destination)
            copy_plain(source,destination)
    required_firmware=tree/'usr/lib/firmware/brcm'
    if not any(required_firmware.glob('brcmfmac43455-sdio*')):
        raise ValueError('wifi_firmware_missing')
    # Preserve a dependency-complete RAM userspace for both storage daemons.
    mirror_userspace(tree,work/'runtime')
    runtime_code=work/'runtime/usr/lib/sd-netboot'
    runtime_code.mkdir(parents=True)
    for name in ['boot.py','wifi_config.py']:
        shutil.copy2(FILES/name,runtime_code/name)
    command(['chroot',work/'runtime','/usr/bin/python3.14','-I','-c',
             "import sys;sys.path.insert(0,'/usr/lib/sd-netboot');import boot,ssl,ctypes,hashlib;assert hashlib.pbkdf2_hmac('sha1',b'password',b'IEEE',4096,32).hex().startswith('f42c6fc5')"])
    command(['chroot',work/'runtime','/usr/bin/wpa_supplicant','-v'])
    command(['chroot',work/'runtime','/usr/lib/systemd/systemd-networkd','--version'])
    # An exitrd gets the same RAM tools and dracut's shutdown implementation.
    shutil.copytree(work/'runtime',work/'exitrd',symlinks=True)
    shutdown=Path('/usr/lib/dracut/modules.d/86shutdown/shutdown.sh')
    shutil.copy2(shutdown,work/'exitrd/shutdown')
    (work/'exitrd/shutdown').chmod(0o755)
    hook=work/'exitrd/usr/lib/dracut/hooks/shutdown'
    hook.mkdir(parents=True)
    shutil.copy2(FILES/'shutdown-network.sh',hook/'99-sd-netboot.sh')
    # dracut shell libraries are required by shutdown as well as early boot.
    for name in ['lib/dracut','usr/lib/dracut']:
        source=tree/name
        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source,work/'exitrd'/name,symlinks=True,dirs_exist_ok=True)
    payload=tree/'usr/lib/sd-netboot'
    payload.mkdir(parents=True,exist_ok=True)
    for name in ['boot.py','wifi_config.py']:
        shutil.copy2(FILES/name,payload/name)
    shutil.move(work/'runtime',payload/'runtime')
    shutil.move(work/'exitrd',payload/'exitrd')
    shutil.copytree(FILES/'units',payload/'units')
    unitdir=tree/'etc/systemd/system'
    unitdir.mkdir(parents=True,exist_ok=True)
    for name in ['sd-netboot-prepare.service','sd-netboot-root.service','sysroot.mount']:
        shutil.copy2(FILES/'units'/name,unitdir/name)
    for target,name in [('initrd.target','sd-netboot-root.service'),('initrd-root-fs.target','sysroot.mount')]:
        wants=unitdir/(target+'.requires')
        wants.mkdir(exist_ok=True)
        (wants/name).symlink_to('../'+name)
    for stage,name,dest in [('cmdline','cmdline.sh','01-sd-netboot.sh'),('initqueue/finished','root-ready.sh','99-sd-netboot.sh')]:
        directory=tree/'usr/lib/dracut/hooks'/stage
        directory.mkdir(parents=True,exist_ok=True)
        shutil.copy2(FILES/name,directory/dest)
    archive=work/'initramfs.cpio'
    newc_write(tree,archive)
    output=work/'initramfs-pxe-wifi.img'
    with open(output,'xb') as stream:
        command(['gzip','-n','-9','-c',archive],stdout=stream)
    return output


def verify_image(image, output, manifest):
    with open(image,'rb') as f:
        header=f.read(512)
    if header[510:512]!=b'\x55\xaa' or struct.unpack_from('<I',header,454)[0]!=2048:
        raise ValueError('partition_table_invalid')
    with tempfile.TemporaryDirectory(dir=output) as directory:
        directory=Path(directory)
        for name,expected in manifest['files'].items():
            destination=directory/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            command(['mcopy','-i',str(image)+'@@1048576','::/'+name,destination])
            if digest(destination)!=expected:
                raise ValueError('image_file_checksum_mismatch')
        command(['mcopy','-i',str(image)+'@@1048576','::/wifi.conf',directory/'wifi.conf'])
        if (directory/'wifi.conf').read_bytes()!=EMPTY_WIFI:
            raise ValueError('image_credentials_not_empty')
        command(['mcopy','-i',str(image)+'@@1048576','::/boot-manifest.json',directory/'manifest.json'])
        if json.loads((directory/'manifest.json').read_text())!=manifest:
            raise ValueError('image_manifest_mismatch')


def main():
    global LOG
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['sysroot','tftp-dir','work-dir','output-dir']:
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ['kernel-release','image-id','source-revision']:
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--size-mib',type=int,default=512)
    args=parser.parse_args()
    if not ID.fullmatch(args.image_id) or not KERNEL.fullmatch(args.kernel_release) or not re.fullmatch('[0-9a-f]{40}',args.source_revision):
        parser.error('invalid build identity')
    for name in ['sysroot','tftp_dir','work_dir','output_dir']:
        path=getattr(args,name)
        if not path.is_absolute():
            parser.error('all paths must be absolute')
        setattr(args,name,path.resolve())
    source_is_readonly(args.sysroot)
    if args.work_dir.exists() or args.output_dir.exists():
        parser.error('work and output paths must be new')
    for path in [args.work_dir,args.output_dir]:
        if path==Path('/') or args.sysroot==path or args.sysroot in path.parents:
            parser.error('work/output cannot be inside sysroot')
    args.work_dir.mkdir(parents=True,mode=0o700)
    args.output_dir.mkdir(parents=True,mode=0o700)
    LOG=args.output_dir/'build.log'
    started=time.monotonic()
    lock=open(args.work_dir.parent/'.sd-netboot-build.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        kernel=args.tftp_dir/'kernel8.img'
        modules=args.sysroot/'usr/lib/modules'/args.kernel_release
        if not modules.is_dir() or not (modules/'modules.dep').is_file():
            raise ValueError('exact_kernel_modules_missing')
        manifest={'schema_version':1,'boot_protocol_version':1,'model':'pi4',
                  'image_id':args.image_id,'source_revision':args.source_revision,
                  'kernel_release':args.kernel_release,'kernel_sha256':digest(kernel),
                  'modules_sha256':tree_digest(modules)}
        initrd=build_initrd(args,args.work_dir,manifest)
        manifest['initramfs_unpacked_bytes']=(args.work_dir/'initramfs.cpio').stat().st_size
        boot=args.work_dir/'boot'
        boot.mkdir()
        for name in ['kernel8.img','start4.elf','fixup4.dat','bcm2711-rpi-4-b.dtb']:
            copy_plain(args.tftp_dir/name,boot/name)
        if (args.tftp_dir/'overlays').is_dir():
            copy_plain(args.tftp_dir/'overlays',boot/'overlays')
        shutil.copyfile(initrd,boot/'initramfs-pxe-wifi.img')
        signature=int(hashlib.sha256(args.image_id.encode()).hexdigest()[:8],16) or 1
        manifest['partuuid']=f'{signature:08x}-01'
        (boot/'config.txt').write_text('[all]\narm_64bit=1\nenable_uart=1\n[pi4]\nkernel=kernel8.img\ninitramfs initramfs-pxe-wifi.img followkernel\ncmdline=cmdline.txt\n[all]\n')
        (boot/'cmdline.txt').write_text('console=serial0,115200 console=tty1 root=sd-netboot rw rd.shell=0 rd.emergency=halt rd.hostonly=0 sd_netboot.partuuid='+manifest['partuuid']+' cgroup_enable=cpuset cgroup_enable=memory cgroup_memory=1\n')
        (boot/'wifi.conf').write_bytes(EMPTY_WIFI)
        (boot/'diagnostics').mkdir()
        (boot/'README.txt').write_text('Pi4 Wi-Fi netboot candidate. Edit wifi.conf after writing the image.\nWPA2-Personal/CCMP, one SSID, IPv4. Values after = are literal; no quotes needed.\nOnly empty credentials are distributed. DHCP must provide root-path (17) and role (224).\nThe selected rootfs must contain the matching kernel-bundle.json; see the external rootfs-kernel-bundle.json.\nPi4 cold boot, real AP authentication and desktop/k3s acceptance still require physical testing.\nOn failure, collect diagnostics/boot-status.txt and boot-events.jsonl after power-off.\n')
        manifest['files']={str(p.relative_to(boot)):digest(p) for p in sorted(boot.rglob('*')) if p.is_file() and p.name!='wifi.conf'}
        (boot/'boot-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        (args.output_dir/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        marker={k:manifest[k] for k in ['boot_protocol_version','kernel_release','kernel_sha256','modules_sha256']}
        (args.output_dir/'rootfs-kernel-bundle.json').write_text(json.dumps(marker,indent=2)+'\n')
        image=args.work_dir/(args.image_id+'.img')
        mbr(image,args.size_mib,signature)
        command(['mformat','-i',str(image)+'@@1048576','-F','-T',str(args.size_mib*2048-2048),'-v','RPI_NETBOOT','::'])
        for item in sorted(boot.iterdir()):
            command(['mcopy','-i',str(image)+'@@1048576','-s',item,'::/'])
        verify_image(image,args.work_dir,manifest)
        raw_sha=digest(image)
        compressed=args.output_dir/(args.image_id+'.img.xz.partial')
        with open(compressed,'xb') as stream:
            command(['xz','-9e','-T1','--stdout',image],stdout=stream)
        command(['xz','--test',compressed])
        p=subprocess.Popen(['xz','-dc',str(compressed)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        h=hashlib.sha256();size=0
        for data in iter(lambda:p.stdout.read(1024*1024),b''):
            h.update(data);size+=len(data)
        if p.wait()!=0 or h.hexdigest()!=raw_sha or size!=image.stat().st_size:
            raise ValueError('compressed_stream_mismatch')
        final=compressed.with_suffix('')
        compressed.rename(final)
        result={'status':'image_created','physical_acceptance':'not_run','image_id':args.image_id,
                'image_file':final.name,'compressed_sha256':digest(final),'raw_sha256':raw_sha,
                'raw_size':size,'compressed_size':final.stat().st_size,'compression':'xz -9e -T1',
                'elapsed_seconds':int(time.monotonic()-started)}
        (args.output_dir/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        (args.output_dir/'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in sorted(args.output_dir.iterdir()) if p.is_file() and p.name not in ['build.log','SHA256SUMS']))
        print(json.dumps(result))
    except Exception as error:
        (args.output_dir/'result.json').write_text(json.dumps({'status':'fail','reason':type(error).__name__,'detail':str(error)[:200]})+'\n')
        raise
    finally:
        fcntl.flock(lock,fcntl.LOCK_UN)
        lock.close()


if __name__=='__main__':
    main()
