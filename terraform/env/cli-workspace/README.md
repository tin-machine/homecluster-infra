# CLI workspace

既存クラスタへ CLI workspace だけを配置する独立した Terraform root。
既定 environment は `production`。Tailscale Operator、SSH 許可鍵を含む既存 Secret、
NAS export は事前に用意する。Operator と Secret の作成・変更は行わない。

実値の tfvars、kubeconfig、backend state、saved plan は repository 外の private path に置く。
`ssh_tailnet_tags` は既存 ACL に対応する値を明示する。
`runtime_class_name` は任意で、指定時は同名 RuntimeClass と node runtime を事前設定する。

```bash
terraform -chdir=terraform/env/cli-workspace init \
  -backend-config=path=/private/workspace/terraform.tfstate
terraform -chdir=terraform/env/cli-workspace plan \
  -var-file=/private/workspace/site.tfvars.json \
  -out=/private/workspace/plan
terraform -chdir=terraform/env/cli-workspace apply /private/workspace/plan
```

CLI の既定上限は CPU 2 core、memory 4Gi。
`nas_mount_options` の既定は `["vers=3", "proto=tcp"]` で、静的 NFS PV/PVC を作る。
PV は Retain / RWX、専用 PVC へ明示的に bind する。capacity 10Gi は bind 用の宣言で NAS quota ではない。
module の既定 `nas_mount_options = []` は従来の inline NFS を維持する。

移行時は次の順番で一度に1 writerだけを起動する。

1. `replicas=0`、`ssh_enabled=false` で namespace と storage を準備する。
2. 旧 workspace を停止し、旧 SSH Service が tailnet hostname を解放するのを待つ。
3. NAS HOME はそのまま使い、別の local-path PVC にある state を移送・照合する。
  WaitForFirstConsumer の state PVC は一時的な移送用 Pod で bind できる。
4. 旧 state を保持したまま、`replicas=1` と必要な SSH 設定で新 workspace を起動する。

クラスタ間で PVC のデータは自動移送されない。既存の SSH ホスト鍵や認証状態を保持するには、
state の所有権、mode、symlink も移送する。tmux の実行中プロセスは移行されない。

mock provider による plan 検証は `terraform test` をこの directory で実行する。
