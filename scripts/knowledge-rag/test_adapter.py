"""Executable parser/policy contracts; no live corpus or model required."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import adapter


class ParserContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'RAG_CHUNK_PROFILE':'upstream','KNOWLEDGE_RAG_DIR':str(self.root)})
        self.env.start()
        self.server = adapter.install()
        self.cfg = patch.multiple(self.server.config, documents_dir=self.root,
                                  supported_formats=['.md','.py','.sh'], exclude_patterns=['site/public'])
        self.cfg.start()
        self.parser = self.server.DocumentParser()

    def tearDown(self):
        self.cfg.stop(); self.env.stop(); self.tmp.cleanup()

    def file(self, name, text, executable=False):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(text)
        if executable:p.chmod(0o700)
        return p

    def test_formats_keep_original_path_and_bytes(self):
        for suffix in ('.tf','.j2','.bash','.fish','.toml','.hcl'):
            p=self.file('repo/source'+suffix,'# 日本語の説明\nresource "demo" "example" {}\n')
            doc=self.parser.parse_file(p)
            self.assertEqual(doc.source,p)
            self.assertEqual(doc.content,p.read_text())
            self.assertEqual(doc.format,suffix)
            self.assertTrue(doc.chunks)
        self.assertEqual(len(list(self.parser.iter_files())),6)

    def test_dependencies_pruned_and_authored_directories_retained(self):
        for name in ('a/node_modules/x.md','a/.venv/x.py','a/.terraform/x.tf','site/public/x.md',
                     'go/vendor/lib/x.go','py/env/x.py','rust/target/x.md'):
            self.file(name,'dependency')
        self.file('go/go.mod','module example')
        self.file('py/env/pyvenv.cfg','home = /fixture')
        self.file('rust/Cargo.toml','[package]')
        authored=['repo/bin/a.sh','repo/pkg/a.py','repo/lib/b.py','repo/public/help.md','repo/vendor/own.md']
        for name in authored:self.file(name,'authored')
        files={p.relative_to(self.root).as_posix() for p in self.parser.iter_files()}
        self.assertTrue(set(authored)<=files)
        self.assertFalse(any('/node_modules/' in p or '/target/' in p or '/env/' in p or '/.venv/' in p for p in files))
        self.assertNotIn('go/vendor/lib/x.go',files)
        self.assertNotIn('site/public/x.md',files)
        # Upstream's deletion pass asks about the FILE, not the pruned parent directory.
        self.assertTrue(self.parser._should_exclude(self.root/'go/vendor/lib/x.go',self.root,[]))
        self.assertTrue(self.parser._should_exclude(self.root/'site/public/x.md',self.root,['site/public']))
        self.assertTrue(self.parser._should_exclude(self.root/'py/env/x.py',self.root,[]))

    def test_sensitive_and_symlink_paths_are_not_scanned(self):
        for name in ('a/.env','a/secret.key','a/encrypted/inventory.sops.yml','a/.ssh/config'):
            self.file(name,'fixture only')
        original=self.file('repo/readme.md','safe')
        (self.root/'link.md').symlink_to(original)
        (self.root/'dir-link').symlink_to(original.parent,target_is_directory=True)
        self.assertEqual(list(self.parser.iter_files()),[original])

    def test_extensionless_script_and_binary_rejection(self):
        script=self.file('repo/scripts/run','#!/bin/sh\necho example\n',True)
        self.file('repo/binary','\x7fELF\x00',True)
        self.file('repo/without-shebang','arbitrary text',True)
        self.assertEqual(list(self.parser.iter_files()),[script])
        self.assertEqual(self.parser.parse_file(script).source,script)
        binary=self.file('repo/binary.tf','a\0b')
        with self.assertRaises(ValueError):self.parser.parse_file(binary)
        with patch.dict(os.environ,{'RAG_MAX_TEXT_BYTES':'2'}):
            with self.assertRaises(ValueError):self.parser.parse_file(script)

    def test_missing_source_stops_before_scan(self):
        self.file('a.md','safe')
        with patch.dict(os.environ,{'RAG_SOURCE_ID':'fixture-123'}):
            with self.assertRaises(FileNotFoundError):list(self.parser.iter_files())
            self.file('.rag-source-id','wrong')
            with self.assertRaises(RuntimeError):list(self.parser.iter_files())
            self.file('.rag-source-id','fixture-123')
            self.assertEqual(len(list(self.parser.iter_files())),1)

    def test_scan_error_is_not_an_empty_success(self):
        def inaccessible(*args, **kwargs):
            kwargs['onerror'](PermissionError('fixture directory unavailable'))
            yield
        with patch('adapter.os.walk', inaccessible):
            with self.assertRaises(PermissionError):
                list(self.parser.iter_files())

    def test_context_chunks_keep_function_identity_without_execution(self):
        source=self.file('a.py','raise RuntimeError("must never execute")\n\ndef first():\n    """日本語の関数説明。"""\n'+
                         '    x = 123\n'*50+'    return x\n\ndef second():\n    return 7\n')
        with patch.dict(os.environ,{'RAG_CHUNK_PROFILE':'context'}):
            parser=self.server.DocumentParser(chunk_size=160,chunk_overlap=30)
            doc=parser.parse_file(source)
        first=[c for c in doc.chunks if 'x = 123' in c.content]
        self.assertGreater(len(first),1)
        self.assertTrue(all('def first()' in c.content for c in first))
        self.assertTrue(any('def second()' in c.content and 'return 7' in c.content for c in doc.chunks))
        self.assertEqual([c.index for c in doc.chunks],list(range(len(doc.chunks))))


if __name__=='__main__':unittest.main()
