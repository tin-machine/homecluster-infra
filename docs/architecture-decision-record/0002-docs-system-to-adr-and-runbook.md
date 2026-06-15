---
status: accepted
audience: human-ai
scope: docs-system-to-adr-and-runbook
last_reviewed: 2026-05-31
---

# ADR 0002: `docs/system/` を ADR と runbook に分解する

## 状況

現行の `docs/system/` には、設計判断、構成説明、実装メモ、確認 command、private な運用 context が混在している。

公開 repository では、判断理由と trade-off は残したい。一方で、実 host / IP / path、raw log、復旧手順、長い時系列は公開範囲に入れない。

単純に `docs/system/` を `docs/architecture-decision-record/` へ rename すると、ADR と呼べない運用 context まで公開側に残りやすい。

## 決定

公開側では、`docs/system/` をそのまま残さない。

公開可能な設計判断だけを ADR として書き直し、公開側の [ADR index](README.md) から辿れる形にする。operation 手順、troubleshooting 原本、実環境固有 context は private runbook 側へ移す。

ADR は次を含める。

- 状況。
- 決定。
- 採用理由。
- 不採用案。
- 影響。
- 見直し条件。

ADR は次を含めない。

- 実 host 名、private IP、MAC、serial、credential。
- 長い raw log。
- 一回限りの復旧手順。
- private repository 名や private URL。

## 理由

設計判断は公開しても価値があるが、運用原本は家庭内環境の topology や実運用境界を推測させる。

ADR と runbook を分けることで、公開側 docs は判断理由の再利用に集中し、private 側 docs は復旧と検証の再開性を維持できる。

## 不採用案

### `docs/system/` をそのまま公開する

不採用。

構成説明と private context が混ざっており、redaction 漏れの可能性が高い。

### `docs/system/` を単純 rename する

不採用。

ADR の粒度ではない文書が混ざり、公開側で読む人にとっても責務が曖昧になる。

### 全 docs を private 側へ移し、公開側に docs を置かない

不採用。

公開 repository で実装だけが残り、なぜその構成にしたかを追えなくなる。

## 影響

既存 docs の link 更新が必要になる。

公開側 [README](../../README.md) では ADR を入口にし、詳細な operation / troubleshooting は private runbook 側へあることだけを示す。

## 見直し条件

- ADR が増えすぎて、domain-specific docs を併設した方が読みやすくなった場合。
- 公開側で実装に近い system overview が必要になり、ADR だけでは navigation が弱くなった場合。
