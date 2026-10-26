import argparse
import unittest

from http_client import completed
from render_poc import render


class RuntimeContracts(unittest.TestCase):
    def test_failed_or_missing_reindex_result_is_never_success(self):
        for value in ({}, {'active':True}, {'active':False}, {'active':False,'last_error':'NFS missing'},
                      {'active':False,'last_result':{'errors':1}},
                      {'active':False,'last_result':{'skipped_reason':'reindex_already_running'}}):
            with self.subTest(value=value),self.assertRaises(RuntimeError):completed(value)
        self.assertEqual(completed({'active':False,'last_result':{'errors':0,'indexed':2}})['indexed'],2)

    def test_poc_requires_immutable_image_and_distinct_nodes(self):
        args=argparse.Namespace(namespace='rag-stg',image='registry.example.invalid/rag@sha256:'+'a'*64,
            node='worker-a',editor_node='worker-b',source_id='fixture',nfs_storage_class='nfs-client',chunk_size=400,exclude=[])
        items=render(args)['items']
        state=next(x for x in items if x['kind']=='StatefulSet')['spec']
        self.assertEqual(state['replicas'],1)
        pod=state['template']['spec']
        rag=pod['containers'][0]
        self.assertTrue(next(v for v in rag['volumeMounts'] if v['name']=='corpus')['readOnly'])
        self.assertFalse(pod['automountServiceAccountToken'])
        args.image='registry.example.invalid/rag:latest'
        with self.assertRaises(ValueError):render(args)
        args.image='registry.example.invalid/rag@sha256:'+'a'*64
        args.editor_node=args.node
        with self.assertRaises(ValueError):render(args)
        args.editor_node='worker-b';args.namespace='production'
        with self.assertRaises(ValueError):render(args)


if __name__=='__main__':unittest.main()
