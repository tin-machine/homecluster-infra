---
status: current
audience: operator
scope: inventory-boundary
last_reviewed: 2026-05-31
---

# Inventory 保存方式の候補

## 目的

実 inventory を公開 repository に置かず、各 repository から `../inventory.yml` として参照できる状態を保つための保存方式を整理する。

この文書は保存方式の公開可能な比較である。実 host、address、secret、private repository URL、SOPS recipient、復号鍵 path は扱わない。

## 前提

- 実 inventory はこの repository に置かない。
- runtime inventory path は各 repository root から見た `../inventory.yml` とする。
- `../inventory.yml` は site-local な file であり、public CI は前提にしない。
- raw secret は private repository であっても平文 Git 管理しない。

## 候補

### A. 親 directory の symlink

workspace 直下の `inventory.yml` を、private inventory repository 内の `inventory.yml` へ symlink する方式。

```text
workspace/
  inventory.yml -> private-inventory/inventory.yml
  private-inventory/
    inventory.yml
  homecluster-infra/
```

メリット:

- `ansible-playbook -i ../inventory.yml ...` の実行感を維持しやすい。
- 仕組みが単純で、local operator が理解しやすい。

デメリット:

- symlink 先の `inventory.yml` を平文 file として持つか、復号済み file として持つかを別途決める必要がある。
- private repository 内に平文 inventory を置くと、private repository 漏洩時の影響が大きい。

現時点の評価:

- 採用しない寄り。
- generated entrypoint が使えない場合の単純な fallback 候補として残す。

### B. 生成済み entrypoint

private 側の encrypted source から workspace 直下の `inventory.yml` を生成する方式。

```text
workspace/
  inventory.yml                         # generated local file, not committed
  private-inventory/
    encrypted/inventory.sops.yaml       # 正本
    scripts/render-inventory
    scripts/check-inventory
  homecluster-infra/
```

メリット:

- `../inventory.yml` contract を維持できる。
- Git 上の正本を encrypted source にできる。
- 生成物を Git 管理しなければ、public repository と private repository の境界を保ちやすい。
- Codex / local automation が非対話で復号できる構成にしやすい。

デメリット:

- 生成 command と検証 command が必要になる。
- 生成忘れ、古い生成物、復号済み file の誤 commit を防ぐ guard が必要になる。

現時点の評価:

- 採用方針。
- SOPS age encrypted source から `../inventory.yml` を生成し、mode `0600` と Git tracking の有無を確認する。

### C. checkout 内の復号済み file

private inventory repository 内に encrypted source を置き、apply 前に同 repository 内の `inventory.yml` へ復号する方式。

```text
private-inventory/
  encrypted/inventory.sops.yaml
  inventory.yml                         # decrypted local file, not committed
```

メリット:

- encrypted source と復号済み entrypoint を同じ repository の中に置ける。
- private inventory repository 単体では構造が分かりやすい。

デメリット:

- 各 repository からの標準 path `../inventory.yml` を維持するには、追加で symlink、copy、または wrapper が必要になる。
- 復号済み `inventory.yml` の誤 commit リスクが残る。

現時点の評価:

- 主方式にはしない。
- private repository 単体で検証する用途の補助候補として扱う。

### D. 外部 secret injection

Git には inventory 構造や secret reference だけを置き、実 secret は外部 secret store から実行時に注入する方式。

候補例:

- password store
- OS keychain
- cloud secret manager
- HashiCorp Vault
- Kubernetes Secret
- environment injection

メリット:

- GitHub repository 漏洩時に secret 本体を分離できる。
- 高価値 token、kubeconfig、Terraform credential の rotation / revoke と相性がよい。

デメリット:

- secret store の bootstrap と unlock が必要になる。
- offline 復旧や初期構築では依存が増える。
- 家庭内検証環境では運用が重くなりやすい。

現時点の評価:

- inventory 全体の主方式にはしない。
- high-value credential の退避先として個別採用する。

## 採用方針

当面は **B. generated entrypoint** を採用する。

実 inventory の正本は private 側の SOPS age encrypted source とし、`../inventory.yml` は local generated file として扱う。Ansible 実行前には、生成と検証を行う。

```bash
scripts/render-inventory
scripts/check-inventory
```

public repository では、実 inventory ではなく `examples/inventory.yml` を使う。

```bash
ansible-playbook --syntax-check -i examples/inventory.yml ansible/openwrt/site.yml
```

## 注意点

- public docs には real host、private IP、private path、SOPS recipient、復号鍵 path、secret 値を載せない。
- `../inventory.yml` は生成物であり、Git に追加しない。
- private repository でも raw secret の平文 Git 管理は避ける。
- `ADR 0010` は inventory boundary の決定を扱い、この文書は保存方式の比較を扱う。
