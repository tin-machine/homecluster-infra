# knowledge-rag CPU / ARM64 staging PoC

knowledge-rag 4.11.1 の単一HTTPサーバーに、コード読込みと原本確認を追加する。
これは非機密文書によるstaging検証用。検索品質の受入とクライアントの常用切替は別に判定する。

## 構成

- StatefulSetは1 replica。ChromaDBとモデルは別々のblock filesystem PVCへ保存する。
- 文書PVCはNFS。RAGはread-only、別nodeの検証editorはread-writeでmountする。
- CPUは最大2 core、RAG memory上限4 GiB。GPU用packageを含めない。
- `http_client.py`が60秒おきに既存サーバーへ差分更新を依頼する。モデルやDBを開かない。
- inotify watcherは無効。NFSの別clientによる編集は定期scanで検出する。
- HTTPはClusterIPのみ。NetworkPolicyは同namespaceのeditorだけから接続を許可する。
  手元の検証は認証済みSSHとloopbackのport-forwardを使う。Bearerは未設定。
- `/health`はprocess生存確認。索引の更新成功は`get_reindex_status`の結果と検索で判定する。

## 読込みと除外

`adapter.py`はupstreamのversionを検査してparserを差し替える。`.tf`、`.j2`、`.hcl`、
`.toml`などのUTF-8 text、shebang付き実行可能scriptを追加し、元pathを保持する。
追加形式はNULを含むfile、2 MiB超のfile、不正なUTF-8を拒否する。

`.git`、`node_modules`、`.terraform`、仮想環境、言語cache、既知のcredential pathを除外する。
Go/Composer/Rubyのvendor、Cargo/Mavenのtargetは親manifestの存在で判定する。
`bin`、`lib`、`pkg`、`public`を一律除外しない。repository固有の生成物や外部ライブラリは
`render_poc.py --exclude 'repo/generated'`のように追加する。各checkoutのpathへ適用すること。
ファイル名による除外はsecret検出の完全性を保証しないため、公開可能なcorpusを選んで投入する。

source marker `.rag-source-id`とNFS mountを各scanの前に確認する。
directory読込み失敗は例外にし、正常な空scanへ変換しない。
NFSの通信断によるkernel I/O待ちはこのguardの対象外であり、更新時間超過を別に監視する。

`RAG_CHUNK_PROFILE=context`では文書titleを各chunkへ付け、Pythonの関数/class境界を
ASTで分割する。コードは実行しない。`upstream`で従来分割へ戻せる。
chunk条件を変更する場合は新しいdata PVCで比較するか、明示的に全再Embeddingする。

## Modelと依存の固定

既定は多言語MiniLMの量子化ONNX。`model.lock.json`に配布元、revision、SHA256を固定し、
init containerで検証して取得する。main containerはoffline・cache read-onlyで動く。
E5-small用lockは比較再現用。採用モデルとして扱わない。
モデル・chunk・prefix・次元を変更したDBを使い回さない。

`requirements.lock`はPython 3.12 / ARM64 / glibc 2.28以上向け。
次のコマンドで再解決し、差分をreviewする。

```bash
uv pip compile requirements.in --python-version 3.12 \
  --python-platform aarch64-manylinux_2_28 --generate-hashes -o requirements.lock
```

## 検証とbuild

依存を入れたPython 3.12環境で実行する。

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest -v test_adapter test_runtime_contract
docker buildx build --platform linux/arm64 --tag "$RAG_IMAGE_TAG" --push .
```

build host上でARM64 wheelを取得し、ARM64 Python imageへcopyする。
QEMUでEmbeddingを実行したり、モデルを再学習したりするbuildではない。
取得するwheelはhash照合する。modelはimageに含めずcache PVCへ保存する。

## Manifest生成と投入順

対象clusterがstagingであること、node、NFS StorageClass、SSD-backed local-path、
空き容量、既存同名resourceがないことを実行前に照合する。renderer自身はcluster identityを検証しない。

```bash
python render_poc.py --image "$RAG_IMAGE_DIGEST" \
  --node "$RAG_NODE" --editor-node "$EDITOR_NODE" \
  --source-id "$RAG_SOURCE_ID" --chunk-size 400 > rag-poc.json
```

1. Namespace、StorageClass、ConfigMap、PVC、Service、NetworkPolicy、editor Podを作る。
2. editor Pod経由で専用corpus PVCへ公開可能なfixtureをcopyし、`.rag-source-id`を書く。
3. StatefulSetを作る。initでモデルを検証した後にHTTPサーバーが起動する。
4. `python http_client.py --url "$RAG_MCP_URL" --action get_index_stats`と検索を確認する。
5. 別nodeのeditorから追加・変更・rename・削除し、更新結果と検索結果を確認する。
6. 複数HTTP client、CPU provider、PSS/SwapPss、metrics、Pod再作成後の永続化を確認する。

定期更新はmtime/sizeを使う。内容を変更して同じmtime/sizeに戻した場合は通常scanで
検出できないため、`python http_client.py --force --url "$RAG_MCP_URL"`を明示実行する。
forceは全件を再Embeddingし得るので日常のtimerには指定しない。

rollbackは専用StatefulSetを0 replicaにし、editorを停止する。PVCとPVは保持する。
local-pathの要求容量はhard quotaではない。NFS quotaもStorageClass側の実装に依存するため、
空き容量と実使用量を確認する。一般のNAS exportや既存homeはこのPoCで変更しない。
