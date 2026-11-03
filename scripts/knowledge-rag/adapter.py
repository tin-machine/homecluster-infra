"""Small, version-checked extensions for a shared CPU knowledge-rag service.

Original paths and bytes remain authoritative. No source files are rewritten.
"""
import ast
from collections import Counter
import fnmatch
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import sys

EXTRA_FORMATS = set('''.tf .hcl .j2 .jinja .jinja2 .toml .ini .cfg .conf
    .fish .lua .vim .css .scss .html .htm .mdx .rst .adoc .nix .tex .rb .php
    .java .cs .swift .svelte .vue .ex .exs .bat .ps1 .bash .zsh .mjs .cjs
    .mts .cts .hpp .hh .cxx .service .timer .rules .desktop .r .pl .ahk
    .kts .jsonc .tsv .plist'''.split())
EXTRA_NAMES = {'go.mod', 'go.sum', '.gitignore', '.gitattributes', '.editorconfig'}
DEPENDENCIES = set('''.git node_modules .terraform .terragrunt-cache .venv
    __pycache__ .pytest_cache .mypy_cache .ruff_cache .tox .nox .pnpm-store
    .gradle .m2 .npm .pio'''.split())
SENSITIVE = {'.ssh', '.gnupg', '.aws', '.kube', 'secrets', 'credentials', 'encrypted'}
SENSITIVE_NAMES = {'inventory.yml', 'inventory.yaml', 'inventory-new.yml', 'kubeconfig',
    'credentials.json', '.netrc', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519'}


def denied(path, root, patterns=()):
    """Common rules plus caller-owned path rules; no blanket bin/lib/pkg/public exclusion."""
    rel = path.relative_to(root)
    for part in rel.parts:
        if part in DEPENDENCIES or part in SENSITIVE:
            return True
    name = path.name.lower()
    if (name in SENSITIVE_NAMES or '.sops.' in name or '.tfstate' in name
            or path.suffix.lower() in {'.key', '.pem', '.p12', '.pfx', '.age'}
            or name == '.env' or (name.startswith('.env.') and not name.endswith(('.example', '.sample', '.template')))):
        return True
    key = '/' + rel.as_posix() + '/'
    if any(p in key for p in ('/.yarn/cache/', '/.yarn/unplugged/', '/.cargo/registry/',
                              '/.cargo/git/checkouts/', '/pkg/mod/')):
        return True
    ancestors = [root.joinpath(*rel.parts[:i]) for i in range(1, len(rel.parts)+1)]
    for candidate in ancestors:
        if candidate.is_symlink():
            return True
        name = candidate.name
        if name in {'venv', 'env', 'virtualenv'} and (candidate/'pyvenv.cfg').is_file():
            return True
        if name == 'vendor' and any((candidate.parent/x).is_file() for x in ('go.mod', 'composer.json', 'Gemfile')):
            return True
        if name == 'target' and any((parent/x).is_file() for parent in (candidate.parent, candidate.parent.parent)
                                    for x in ('Cargo.toml', 'pom.xml')):
            return True
    return any(any(fnmatch.fnmatchcase(x.relative_to(root).as_posix(), p) for x in ancestors)
               or any(fnmatch.fnmatchcase(x, p) for x in rel.parts)
               for p in patterns)


def check_source(root):
    """Fail before enumeration/pruning if the configured source identity is absent."""
    expected = os.environ.get('RAG_SOURCE_ID')
    if not expected:
        return  # Isolated local fixtures do not claim NFS acceptance.
    marker = root/'.rag-source-id'
    if marker.is_symlink() or marker.read_text().strip() != expected:
        raise RuntimeError('corpus source marker mismatch')
    if os.environ.get('RAG_REQUIRE_NFS') == '1':
        found = False
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            mount = left.split()[4].replace('\\040', ' ')
            if Path(mount) == root and right.split()[0] in {'nfs', 'nfs4'}:
                found = True
        if not found:
            raise RuntimeError('corpus is not the expected NFS mount')


def install():
    if importlib.metadata.version('knowledge-rag') != '4.11.1':
        raise RuntimeError('adapter requires reviewed knowledge-rag 4.11.1')
    from mcp_server import server
    from mcp_server.config import config
    from mcp_server.ingestion import DocumentParser
    if getattr(server.DocumentParser, '_shared_adapter', False):
        return server

    class SharedParser(DocumentParser):
        _shared_adapter = True

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._parsers.update({ext: self._strict_text for ext in EXTRA_FORMATS | EXTRA_NAMES})
            self._parsers['<script>'] = self._strict_text
            self.scan_counts = Counter()

        def _strict_text(self, path):
            limit = int(os.environ.get('RAG_MAX_TEXT_BYTES', '2097152'))
            with path.open('rb') as handle:
                data = handle.read(limit + 1)
            if len(data) > limit or b'\0' in data:
                raise ValueError('text file is binary or exceeds configured size bound')
            text = data.decode('utf-8-sig', errors='strict')
            return text, {'type':'code', 'title':path.stem, 'language':path.suffix.lstrip('.') or 'script',
                          'file_size':len(data), 'line_count':text.count('\n')+1}

        def _select_parser_key(self, filepath, logical_filename=None):
            try:
                return super()._select_parser_key(filepath, logical_filename)
            except ValueError:
                if logical_filename is not None or filepath.suffix or not self._is_script(filepath):
                    raise
                return '<script>'

        @staticmethod
        def _is_script(path):
            if path.suffix or not path.stat().st_mode & 0o111:
                return False
            with path.open('rb') as handle:
                first = handle.read(256)
            return first.startswith(b'#!') and b'\0' not in first

        @staticmethod
        def _should_exclude(path, base_dir, patterns):
            return denied(path, base_dir, patterns)

        def iter_files(self, directory=None):
            root = Path(directory or config.documents_dir).absolute()
            check_source(root)
            self.scan_counts = Counter()
            supported = set(config.supported_formats) | EXTRA_FORMATS | EXTRA_NAMES
            def scan_error(exc):
                raise exc  # Never turn an unreadable directory into mass deletion.
            for current, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
                base = Path(current)
                kept = []
                for name in dirs:
                    p = base/name
                    if p.is_symlink() or denied(p, root, config.exclude_patterns):
                        self.scan_counts['excluded_directories'] += 1
                    else:
                        kept.append(name)
                dirs[:] = sorted(kept)
                for name in sorted(files):
                    p = base/name
                    if p.is_symlink() or name.startswith('.rag-') or denied(p, root, config.exclude_patterns):
                        self.scan_counts['excluded_files'] += 1
                        continue
                    if not stat.S_ISREG(p.stat().st_mode):
                        self.scan_counts['special_files'] += 1
                        continue
                    if p.suffix.lower() in supported or name in supported or self._is_script(p):
                        self.scan_counts['selected_files'] += 1
                        yield p
                    else:
                        self.scan_counts['unsupported_files'] += 1
            print('corpus_scan '+json.dumps(self.scan_counts), file=sys.stderr)

        def parse_file(self, filepath, *, logical_filename=None):
            doc = super().parse_file(filepath, logical_filename=logical_filename)
            if doc is None or os.environ.get('RAG_CHUNK_PROFILE', 'upstream') == 'upstream':
                return doc
            if os.environ.get('RAG_CHUNK_PROFILE') != 'context':
                raise ValueError('unknown chunk profile')
            # AST is parsed, never imported/executed. Retain upstream chunking on invalid Python.
            if Path(filepath).suffix == '.py':
                try:
                    tree = ast.parse(doc.content)
                except SyntaxError:
                    return doc
                lines = doc.content.splitlines(keepends=True)
                nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                cuts = {0, len(doc.content)}
                labels = {}
                for node in nodes:
                    start = sum(map(len, lines[:node.lineno-1]))
                    cuts.add(start)
                    labels[start] = lines[node.lineno-1].strip()
                offsets = sorted(cuts)
                chunks = []
                for start, end in zip(offsets, offsets[1:]):
                    context = labels.get(start, '')
                    for chunk in self._chunk_text(doc.content[start:end], doc.metadata):
                        chunk.index = len(chunks)
                        chunk.start_char += start; chunk.end_char += start
                        if context and not chunk.content.startswith(context):
                            chunk.content = context+'\n'+chunk.content
                        chunks.append(chunk)
                doc.chunks = chunks
            for chunk in doc.chunks:
                title = doc.metadata.get('title', '')
                if title and not chunk.content.startswith(title):
                    chunk.content = title+'\n'+chunk.content
            return doc

    server.DocumentParser = SharedParser
    model_path = os.environ.get('RAG_MODEL_PATH')
    if config.embedding_model == 'intfloat/multilingual-e5-small':
        if not model_path or not Path(model_path, 'model.onnx').is_file():
            raise RuntimeError('pinned E5 model directory is required')
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType
        TextEmbedding.add_custom_model(model='intfloat/multilingual-e5-small', pooling=PoolingType.MEAN,
            normalization=True, sources=ModelSource(hf='intfloat/multilingual-e5-small'),
            dim=384, model_file='model.onnx', license='MIT', size_in_gb=.47)
    original = server.FastEmbedEmbeddings.__init__
    def embedding_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self._init_kwargs['threads'] = int(os.environ.get('RAG_EMBED_THREADS', '2'))
        if model_path:
            self._init_kwargs.update(specific_model_path=model_path, local_files_only=True)
    server.FastEmbedEmbeddings.__init__ = embedding_init
    return server


if __name__ == '__main__':
    app = install()
    check_source(app.config.documents_dir)
    app.main()
