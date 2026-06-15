---
status: accepted
audience: human-ai
scope: k3s-agent-identity-cache-boundary
last_reviewed: 2026-05-31
---

# ADR 0007: k3s agent identity と containerd cache を分けて扱う

## 状況

PXE root overlay は reboot で消える。一方で、[k3s agent の data-dir](../../ansible/arm64/roles/k3s_local_storage/tasks/main.yml) を local block backed filesystem に置くと、containerd cache や k3s agent identity は reboot 後も残る。

control-plane datastore が再生成された場合、agent 側に古い node identity が残ると、node password mismatch や node authorization failure が起きることがある。

同時に、containerd image cache は保持したい。毎回 image cache を消すと、reboot convergence が遅くなり、registry や network の一時障害にも弱くなる。

## 決定

k3s agent identity と containerd cache を分けて扱う。

PXE boot boundary で server 側 state と agent identity の整合が崩れる可能性がある場合、[agent cert、node password、kubeconfig など identity file を reset](../../ansible/arm64/roles/k3s_local_storage/templates/k3s-node-password-sync.sh.j2) する。

containerd image cache は原則保持する。cache cleanup は identity reset とは別操作にする。

## 理由

identity reset と image cache cleanup を混ぜると、原因切り分けが難しくなる。

identity だけ reset すれば、server datastore 再生成後でも agent を再 join しやすい。

cache を保持すれば、image pull / unpack の負荷を抑えられ、reboot 後の収束が速くなる。

## 不採用案

### data-dir 全体を毎回削除する

不採用。

identity mismatch は解消できるが、containerd cache も失われる。重い image を毎回 pull / unpack することになり、reboot convergence が悪化する。

### identity を永続化し続ける

不採用。

control-plane datastore が再生成される構成では、古い identity が残ること自体が障害原因になる。

### cache を再現性の証明に使う

不採用。

cache があることで起動した Pod は、registry や image digest の正しさを証明しない。fresh pull gate は別に持つ。

## 影響

boot 時の [helper](../../ansible/arm64/roles/k3s_local_storage/templates/k3s-node-password-sync.sh.j2) は、削除対象を identity file に限定する必要がある。

同一 boot 内の k3s restart では、不要に identity を破棄しない。

image cache pruning が必要な場合は、容量対策または registry 検証として別手順にする。

## 見直し条件

- control-plane datastore が安定して永続化され、boot boundary で identity mismatch が起きなくなった場合。
- image cache 保持による stale image / registry drift の事故が増えた場合。
