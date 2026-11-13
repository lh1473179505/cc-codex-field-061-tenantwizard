# 输出约定

`--out` 目录：

- `filings.json`：每个租户一条最终申报，字段 `tenant`、`filing_id`、`legal_name`、`tax_id`、`year`、`amount`。按 tenant 排序。
- `journal.jsonl`：见 docs/portal.md。
- 若发生过撤回，`rollbacks.json` 记录 `[{"tenant":"globex","discarded_draft_id":"..."}]`。

`verify` 核对 filings 的业务字段、两个租户 filing_id 不同、journal 里每个租户都有登录和三步向导，以及 globex 有一次 rollback。
