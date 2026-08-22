# Codex CLI role

ARM64 Gentoo hostへNode.js、Bubblewrap、公式npm package `@openai/codex` を導入します。public defaultは無効です。

このroleはCLI binaryの導入とversion確認だけを担当します。ChatGPT sign-in、API key、`auth.json`、Codex config、provider設定は管理せず、operator-owned runtime inputとしてrepository外に置きます。

## 実行入口

```bash
ansible-playbook -i ../inventory.yml \
  ansible/arm64/playbooks/codex-cli.yml
```

既定ではnpm上のlatestを導入します。versionを固定する場合は外部inventoryで次を指定します。

```yaml
codex_cli_use_latest: false
codex_cli_version: "<reviewed-version>"
```
