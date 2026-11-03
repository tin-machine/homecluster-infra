"""Render a dedicated staging PoC. Does not contact or mutate a cluster."""
import argparse
import json
import re

import yaml


def render(args):
    if not re.fullmatch(r'[a-z0-9-]+-stg', args.namespace):
        raise ValueError('namespace must end in -stg')
    if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', args.image):
        raise ValueError('immutable image digest is required')
    if args.node == args.editor_node:
        raise ValueError('NFS acceptance requires a different editor node')
    ns=args.namespace
    def resource(kind, name, **fields):
        return {'apiVersion':'v1', 'kind':kind, 'metadata':{'name':name,'namespace':ns}, **fields}
    security={'runAsUser':503,'runAsGroup':1000,'runAsNonRoot':True,'seccompProfile':{'type':'RuntimeDefault'}}
    container_security={'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}
    config={
        'paths':{'documents_dir':'/corpus','data_dir':'/data','models_cache_dir':'/models'},
        'models':{'embedding':{'model':'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2','gpu':'false','dimensions':384},'reranker':{'enabled':False}},
        'documents':{'supported_formats':['.md','.txt','.py','.go','.rs','.ts','.tsx','.js','.jsx','.c','.cpp','.h','.sh','.yml','.yaml','.json','Dockerfile','Makefile'],
                     'exclude_patterns':args.exclude,'chunking':{'chunk_size':args.chunk_size,'chunk_overlap':args.chunk_size//5},
                     'batch_size':32,'embed_batch_size':4,'parallel_workers':1},
        'server':{'host':'0.0.0.0','port':8179,'transport':'streamable-http','metrics':{'enabled':True,'port':9179},
                  'rate_limit':{'requests_per_minute':600,'burst':60}},
        'category_mappings':{},'keyword_routes':{},'query_expansions':{},
    }
    env={
        'KNOWLEDGE_RAG_DIR':'/config','KNOWLEDGE_RAG_WATCHER_DISABLED':'1',
        'RAG_SOURCE_ID':args.source_id,'RAG_REQUIRE_NFS':'1','RAG_CHUNK_PROFILE':'context',
        'RAG_MODEL_PATH':'/models/minilm','RAG_EMBED_THREADS':'2',
        'HF_HUB_OFFLINE':'1','HOME':'/tmp','XDG_CACHE_HOME':'/tmp',
    }
    main={'name':'rag','image':args.image,'env':[{'name':k,'value':v} for k,v in env.items()],
          'ports':[{'name':'mcp','containerPort':8179},{'name':'metrics','containerPort':9179}],
          'securityContext':container_security,
          'resources':{'requests':{'cpu':'500m','memory':'1Gi'},'limits':{'cpu':'2','memory':'4Gi'}},
          'volumeMounts':[{'name':n,'mountPath':p,'readOnly':ro} for n,p,ro in [('corpus','/corpus',True),('state','/data',False),('models','/models',True),('config','/config',True),('tmp','/tmp',False)]],
          'startupProbe':{'httpGet':{'path':'/health','port':'mcp'},'periodSeconds':10,'failureThreshold':60},
          'readinessProbe':{'httpGet':{'path':'/health','port':'mcp'},'periodSeconds':10},
          'livenessProbe':{'httpGet':{'path':'/health','port':'mcp'},'periodSeconds':30,'failureThreshold':6}}
    updater={'name':'updater','image':args.image,'command':['python','/opt/rag/http_client.py','--interval','60'],
             'securityContext':container_security,'resources':{'requests':{'cpu':'25m','memory':'96Mi'},'limits':{'cpu':'250m','memory':'192Mi'}}}
    init={'name':'model','image':args.image,'command':['python','/opt/rag/prepare_model.py'],
          'env':[{'name':'RAG_MODEL_PATH','value':'/models/minilm'}], 'securityContext':container_security,
          'resources':{'requests':{'cpu':'100m','memory':'64Mi'},'limits':{'cpu':'1','memory':'192Mi'}},
          'volumeMounts':[{'name':'models','mountPath':'/models'}]}
    labels={'app':'knowledge-rag'}
    objects=[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns,'labels':{'purpose':'knowledge-rag-poc','environment':'staging'}}},
             {'apiVersion':'storage.k8s.io/v1','kind':'StorageClass','metadata':{'name':ns+'-local'},
              'provisioner':'rancher.io/local-path','reclaimPolicy':'Retain','volumeBindingMode':'WaitForFirstConsumer'},
             resource('ConfigMap','rag-config',data={'config.yaml':yaml.safe_dump(config)})]
    for name,storage,size,modes in [('corpus',args.nfs_storage_class,'2Gi',['ReadWriteMany']),('state',ns+'-local','4Gi',['ReadWriteOnce']),('models',ns+'-local','2Gi',['ReadWriteOnce'])]:
        objects.append(resource('PersistentVolumeClaim',name,spec={'accessModes':modes,'storageClassName':storage,'resources':{'requests':{'storage':size}}}))
    objects.append(resource('Service','knowledge-rag',spec={'selector':labels,'ports':[{'name':'mcp','port':8179,'targetPort':'mcp'},{'name':'metrics','port':9179,'targetPort':'metrics'}]}))
    objects.append({'apiVersion':'apps/v1','kind':'StatefulSet','metadata':{'name':'knowledge-rag','namespace':ns},
        'spec':{'replicas':1,'serviceName':'knowledge-rag','selector':{'matchLabels':labels},
                'template':{'metadata':{'labels':labels},'spec':{
                    'automountServiceAccountToken':False,'securityContext':{**security,'fsGroup':1000,'fsGroupChangePolicy':'OnRootMismatch'},
                    'nodeSelector':{'kubernetes.io/hostname':args.node,'kubernetes.io/arch':'arm64'},
                    'terminationGracePeriodSeconds':60,'initContainers':[init],'containers':[main,updater],
                    'volumes':[{'name':n,'persistentVolumeClaim':{'claimName':n}} for n in ['corpus','state','models']]+[
                        {'name':'config','configMap':{'name':'rag-config'}},{'name':'tmp','emptyDir':{'sizeLimit':'128Mi'}}]}}}})
    objects.append(resource('Pod','corpus-editor',metadata={'name':'corpus-editor','namespace':ns,'labels':{'app':'corpus-editor'}},spec={
        'automountServiceAccountToken':False,'securityContext':security,
        'nodeSelector':{'kubernetes.io/hostname':args.editor_node,'kubernetes.io/arch':'arm64'},
        'containers':[{'name':'editor','image':args.image,'command':['python','-c','import time; time.sleep(2147483647)'],
            'securityContext':container_security,'resources':{'requests':{'cpu':'10m','memory':'24Mi'},'limits':{'cpu':'100m','memory':'96Mi'}},
            'volumeMounts':[{'name':'corpus','mountPath':'/corpus'}]}],
        'volumes':[{'name':'corpus','persistentVolumeClaim':{'claimName':'corpus'}}]}))
    objects.append({'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':'restrict-ingress','namespace':ns},
        'spec':{'podSelector':{},'policyTypes':['Ingress'],'ingress':[{'from':[{'podSelector':{'matchLabels':{'app':'corpus-editor'}}}],
               'ports':[{'protocol':'TCP','port':8179}]}]}})
    return {'apiVersion':'v1','kind':'List','items':objects}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--namespace',default='knowledge-rag-stg')
    parser.add_argument('--image',required=True)
    parser.add_argument('--node',required=True)
    parser.add_argument('--editor-node',required=True)
    parser.add_argument('--source-id',required=True)
    parser.add_argument('--nfs-storage-class',default='nfs-client')
    parser.add_argument('--chunk-size',type=int,default=1000)
    parser.add_argument('--exclude',action='append',default=[])
    print(json.dumps(render(parser.parse_args()),indent=2))
