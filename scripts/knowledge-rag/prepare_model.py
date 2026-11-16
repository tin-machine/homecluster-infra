"""Download only reviewed, hash-pinned ONNX data files into persistent storage."""
import hashlib
import json
import os
from pathlib import Path
import urllib.request


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def prepare(target):
    lock=json.loads(Path(os.environ.get('RAG_MODEL_LOCK', str(Path(__file__).with_name('model.lock.json')))).read_text())
    target.mkdir(parents=True,exist_ok=True)
    for name,expected in lock['files'].items():
        if Path(name).name!=name:raise ValueError('invalid model file name')
        dest=target/name
        if dest.is_file() and not dest.is_symlink() and digest(dest)==expected:continue
        remote='/'.join(x for x in (lock.get('remote_directory',''),name) if x)
        url=f"https://huggingface.co/{lock['repository']}/resolve/{lock['revision']}/{remote}"
        pending=target/(name+'.part')
        with urllib.request.urlopen(url,timeout=60) as response,pending.open('wb') as output:
            while block:=response.read(1024*1024):output.write(block)
        if digest(pending)!=expected:raise RuntimeError(f'model hash mismatch: {name}')
        pending.replace(dest)
        print(f'model file verified: {name}',flush=True)
    print('model ready: '+lock['revision'],flush=True)


if __name__=='__main__':
    os.umask(0o077)
    prepare(Path(os.environ['RAG_MODEL_PATH']))
