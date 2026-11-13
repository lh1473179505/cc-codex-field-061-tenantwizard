# tenantwizard

本地财务门户上的多步申报 RPA。夹具站点由本仓库提供，不访问公网。

两个账套要能并发跑完向导，Cookie 和 CSRF 不能串，步骤失败后要撤回草稿并记下审计日志。

```
python -m tenantwizard site --port 8762
python -m tenantwizard run --base http://127.0.0.1:8762 --tenants fixtures/tenants.json --out <out> --checkpoint <ckpt.json>
python -m tenantwizard verify --out <out> --expect fixtures/expected/filings.json
```
