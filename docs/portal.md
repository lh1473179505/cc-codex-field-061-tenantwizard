# 门户行为

`python -m tenantwizard site` 在本地起一个申报门户，不需要浏览器。

## 登录

`GET /login` 返回带隐藏域 `csrf` 的表单。`POST /login` 提交 `username`、`password`、`csrf`，成功后设置 HttpOnly cookie `tw_sid`，并 302 到 `/wizard/1`。csrf 一次性，刷新登录页会换新值。账号见 `fixtures/tenants.json`（仅夹具口令）。

每个 `tw_sid` 只属于一个租户。如果请求带着别的租户的 cookie 去提交，门户会写入错误租户的草稿——这正是当前客户端的问题：`tenantwizard.client.HttpSession` 用了进程内共享的 cookie 罐。

## 向导字段名

步骤页是 `GET /wizard/{1|2|3}`。每个逻辑字段的 input name 不是固定的，而是页面里 `input[data-logical='...']` 的 `name`，由 `sid` 和步骤共同决定，每次登录都会变。必须先读页面再提交，不能写死字段名。

- 第 1 步逻辑字段：`legal_name`、`tax_id`
- 第 2 步逻辑字段：`year`、`amount`、`checksum`。`checksum` 必须等于 `sha256(tax_id + ":" + year + ":" + amount)` 的十六进制前 6 位（小写）。tax_id 用第 1 步已保存到草稿里的值。
- 第 3 步：`confirm` 填 `yes`。成功后返回 JSON，`filing_id` 在 body 里。

每一步还要原样带回页面里的隐藏域 `csrf` 和 `draft_id`。`POST /wizard/{n}` 成功会更新草稿；第 3 步成功后草稿转为正式申报，不能再改。

## 校验失败与撤回

第 2 步 checksum 错误或第 3 步 `confirm` 不是 `yes` 时，门户返回 422，草稿仍停留在失败前的步骤。此时必须对当前 `draft_id` 发 `DELETE /draft/{draft_id}`（同样要带该租户 cookie 和最新 csrf，csrf 在 422 页面或 `GET /draft/{id}` 里）。撤回成功后该草稿消失，同一租户可以重新从第 1 步开新草稿。未撤回就再 POST 第 1 步，门户会 409。

夹具里 globex 的 `fixtures/tenants.json` 提供了两组金额：`amount` 是最终正确值，`amount_first_try` 是故意错误的第一次尝试，用来检查撤回。acme 一次成功即可。

## 并发交错

`run` 必须能在同一个进程里处理 tenants.json 里的全部账套。实现时按租户隔离会话，即使把两个租户的请求交错发出（先登录 A，再登录 B，再继续 A 的第 1 步……）也不能串 cookie。可以用仓库里的客户端，但不要继续用那个共享罐。

## 审计日志

`--out/journal.jsonl` 一行一次 HTTP 调用，字段：`tenant`、`method`、`url`（不含 query 里的无关键）、`status`、`logical_step`（login / wizard-1 / wizard-2 / wizard-3 / rollback / other）、`request_logical_fields`（提交过的逻辑字段名列表，不要写密码或 csrf 的值）、`response_sha256`（响应正文 sha256）。按实际请求顺序追加。
