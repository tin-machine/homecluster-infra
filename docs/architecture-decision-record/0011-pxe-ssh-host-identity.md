---
status: accepted
audience: human-ai
scope: pxe-ssh-host-identity
last_reviewed: 2026-06-04
---

# ADR 0011: PXE SSH host identity は rootfs release から分離する

## 状況

PXE boot した node は NFS rootfs と tmpfs overlay を使う。rootfs release を clean build したり、TFTP / NFS bundle を切り替えたりすると、root filesystem 内の SSH host key は変わり得る。

SSH host key が boot や release 更新で変わると、debug SSH のたびに known_hosts 更新が必要になる。これは運用ノイズになり、実際の接続先確認と一時的な鍵差し替えの区別も難しくする。

## 決定

PXE client の [SSH host identity](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/tasks/pxe_ssh_host_keys.yml) は rootfs release から分離する。

- OpenWrt 側の共有 state に host 別 key store を持つ。
- PXE client は boot 後に DHCP 由来の overlay id を読み、自分の key store から [host key を `/etc/ssh` へ配置](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/pxe-ssh-host-keys.sh.j2)する。
- 初回 boot で key が存在しない場合だけ、client 側で host key を生成して key store へ保存する。
- [`sshd`](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/systemd/sshd-pxe-host-keys.conf.j2) は host key 配置 service の後に起動し、配置に失敗した場合は一時鍵へ fallback させない。
- OpenSSH host certificate は同じ key store に置いた public certificate を使う。
- Host CA private key は PXE client と public repository に置かない。

## 理由

rootfs release と host identity を分けると、rootfs の promote / rollback / rebuild と SSH trust を独立に扱える。

OpenSSH host certificate を使うと、client 側は個別 host key ではなく Host CA を trust anchor にできる。node 数が増えても known_hosts 更新を抑えられる。

fail-closed にしておくと、key store mount や overlay id 解決に失敗した boot が一時鍵で silently 起動することを避けられる。

## 不採用案

### rootfs 内の host key をそのまま使う

不採用。

rootfs rebuild や release 切替で host key が変わり、SSH trust が rootfs lifecycle に引きずられる。

### SSH client 側で known_hosts を毎回捨てる

不採用。

検証時の一時回避としては使えるが、接続先確認を弱める。継続運用の前提にしない。

### Host CA private key を PXE client に配布する

不採用。

client compromise 時の影響が大きい。PXE client には署名済み host certificate だけを置く。

## 影響

key store が利用できない boot では debug SSH が起動しない可能性がある。復旧時は DHCP 由来の overlay id、[bootstrap log mount](../../ansible/openwrt/roles/openwrt_gentoo_rootfs/templates/pxe-bootstrap.sh.j2)、key store の存在を確認する。

共有 key store は家庭内検証環境向けの単純な構成である。強い隔離が必要な場合は、host ごとの export / ACL、local block backed storage、または短命 host key と certificate renewal を組み合わせる。

Host certificate は host key private key の漏洩を無効化しない。host key が漏洩した場合は該当 host key を作り直して再署名する。Host CA private key が漏洩した場合は CA を作り直し、client 側 trust anchor を差し替える。

## 見直し条件

- PXE client 間で host key store の読み取り境界を強める必要が出た場合。
- local block backed storage が全 node で標準化され、host key を local persistent path に置く方が単純になった場合。
- Host CA renewal / rotation を自動化する controller を導入する場合。
