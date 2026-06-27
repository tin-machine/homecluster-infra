---
status: accepted
audience: human-ai
scope: opencode-agent-privilege-taxonomy
last_reviewed: 2026-06-27
---

# ADR 0013: OpenCode agent を privilege boundary で分割する

## 状況

`homecluster-infra` では、OpenCode と local LLM を Ansible / OpenWrt / PXE / k3s 周辺の
source-only 変更補助に使う。

local LLM は、対象 file の探索、edit tool call、validation 実行、失敗修復を一度に任せると、
次のような失敗を起こし得る。

- repository-wide search や長い設計文に token を使い、edit に到達しない。
- 複数 file / 複数 replacement の edit で tool call が壊れる。
- `oldString:` などの edit metadata を source file へ混入する。
- validation が失敗していても、最終報告で成功と要約する。
- 失敗修復時に、狭い修正ではなく関連 file へ探索範囲を広げる。

一方で、完全に使わないのではなく、権限と出力契約を狭めると有効な場面がある。
そのため、OpenCode agent を人間の職能ではなく、blast radius と privilege boundary で分ける必要がある。

## 決定

OpenCode / local LLM は、汎用の trusted autonomous engineer として扱わない。
repository-local agent は、読めるもの、編集できるもの、実行できる command、skill 利用可否、
validation / repair 可否で分割する。

最終判断は常に次の組み合わせで行う。

- raw diff
- deterministic validation
- repository-local review guard
- Codex / operator review

OpenCode の自己申告は validation result として扱わない。
`finish=length`、zero-diff implementation、validation 未実行は失敗として扱う。

Tool 選択の制限は、まず OpenCode 標準の `opencode.json` `permission` で表現する。prompt や
wrapper / shell script は追加の誘導や結果確認には使えるが、OpenCode が標準で `read`、`edit`、
`glob`、`grep`、`list`、`bash`、`task`、`webfetch`、`websearch`、`lsp`、`skill` などを
制御できる場合は、permission を source of truth にする。

現時点で採用済みの基本 agent は次である。

| Agent | 目的 | 主な許可 | 主な禁止 |
| --- | --- | --- | --- |
| `homecluster-ansible-patch` | 1 つの狭い source-only Ansible patch | 選択された skill、bounded read/search、example inventory syntax、validation gate | real inventory、live apply、sysupgrade、Terraform apply、SwitchBot、secret inspection |
| `homecluster-edit-only` | 1 file / 1 replacement の exact edit | edit tool | skill、bash、read/search、validation、live command、secret inspection |

今後追加する場合の優先候補は次である。

| Agent | 目的 | 導入条件 |
| --- | --- | --- |
| `homecluster-read-only` | 実装前に対象 file / anchor / 不確実性だけを返す | 対象範囲が広く、OpenCode に edit まで任せると寄り道が多い場合 |
| `homecluster-validation-runner` | 許可済み validation command だけを実行し compact result を返す | validation と repair を分離したい場合 |
| `homecluster-repair-only` | compact validation JSON と target file だけで 1 failure を最小修復する | guard が failure を十分に特定できる場合 |
| `homecluster-review-only` | current diff の blocker を read-only で確認する | PR 前の同じ手動 review pattern が繰り返される場合 |
| `homecluster-docs-suggest-only` | public-safe 文案や PR body 案を出す | private runbook や raw log を直接編集させたくない場合 |
| `homecluster-converter-only` | deterministic converter script だけを実行する | 既知 pattern の機械変換が繰り返される場合 |

すべての agent は deny-by-default とし、必要な capability だけを allowlist する。
agent を増やす判断基準は、prompt の見た目ではなく permission boundary が実際に変わるかどうかに置く。

OpenCode の permission default は permissive なため、agent ごとに `read` / `glob` / `grep` /
`list` / `task` / `webfetch` / `websearch` / `lsp` / `todowrite` / `question` を明示する。
特に read-only agent でも、`edit` と `bash` だけを deny しても内蔵 `read` / `glob` / `grep`
は残り得る。意図した tool surface は prompt ではなく `opencode.json` で閉じる。

## 理由

local LLM の失敗は、モデル品質だけでなく、tool surface、permission、context 量、prompt 長、
validation の戻し方に依存する。

権限境界を分けると、失敗時の影響範囲が小さくなる。例えば、`homecluster-edit-only` が壊れても、
validation command や live command には到達できない。`homecluster-validation-runner` が失敗しても、
source edit はできない。

agent ごとに failure pattern を分類しやすくなるため、review script や wrapper へ deterministic guard を
追加しやすい。

この設計は ADR 0009 の public CI / local apply 分離、ADR 0010 の inventory 境界、ADR 0012 の
executable public source 方針と整合する。OpenCode は public-safe source の補助には使うが、real
inventory、secret、state、live target へは明示 gate なしに近づけない。

## 不採用案

### 1 つの強い汎用 agent に任せる

不採用。

調査、実装、validation、repair、final review が同じ権限を持つと、失敗時にどの境界で壊れたかが分からない。
また、validation 失敗から自律的に broad search / unrelated edit へ広がりやすい。

### 人間の職能で agent を分ける

不採用。

`実装担当`、`レビュー担当`、`調査担当` のような名前だけでは安全境界にならない。
重要なのは、read / edit / bash / skill / validation / live command の capability が違うことである。

### 最初から候補 agent をすべて実装する

不採用。

agent 数が増えると、OpenCode 設定、permission、prompt、運用判断も増える。
まずは既存の `homecluster-ansible-patch` と `homecluster-edit-only` を維持し、次に
`read-only`、`validation-runner`、`repair-only` の順で必要性を確認する。

### validation と repair を同じ agent に任せる

不採用。

validation failure を見た agent が同じ context で修復まで行うと、failure の意味を都合よく解釈しやすい。
validation は compact JSON で終わらせ、repair は target file と failure JSON に閉じる方が、Codex 側で
review しやすい。

## 影響

- OpenCode agent 追加時は、permission boundary が既存 agent と異なることを確認する。
- prompt は短くし、tool 選択制限は `opencode.json` の permission に置く。詳細な repository rule は
  skill reference または deterministic guard に置く。
- OpenCode implementation run は wrapper 経由で実行し、finish reason、diff 有無、validation result を
  機械的に確認する。
- exact replacement のような小編集では、1 run / 1 file / 1 replacement を優先する。
- read-only scout の実投入では broad grep、recursive `ls`、typo variant の推測探索で token を消費し、
  最終回答前に timeout した。prompt で探索幅を縛るより先に、`opencode.json` で許可 tool を明示する。
- edit-only は prompt だけでは read / glob / grep を完全には抑止できなかった。exact replacement では
  `opencode.json` で `edit` 以外を deny し、OpenCode の自己申告ではなく raw tool trace と diff を確認する。
- OpenCode が生成した差分は、必ず Codex / operator が raw diff と validation output を確認する。
- live apply、real inventory、sysupgrade、Terraform apply、SwitchBot、secret inspection は、この taxonomy の
  source-only agent へ許可しない。

## 見直し条件

- local LLM の tool-call reliability が上がり、複数 replacement / validation / repair を同じ agent に
  任せても deterministic guard で十分に抑止できるようになった場合。
- OpenCode 側の permission model が変わり、read / edit / bash / skill の境界表現が大きく変わった場合。
- agent 数が増えすぎ、運用時にどの agent を使うべきか判断しにくくなった場合。
- public repository で扱う source-only task の範囲が狭まり、OpenCode agent を維持する価値が下がった場合。
