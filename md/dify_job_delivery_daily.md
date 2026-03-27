# Dify 投递日报工作流接入说明

## 目标

这份工作流用于验证一条最小闭环：

1. Dify 收集平台、账号、日期范围
2. 通过 HTTP 节点调用 `Job Help API`
3. 后端返回结构化 `report-pack`
4. LLM 只负责把数据包转换成日报

## 后端接口

工作流依赖以下接口：

```http
POST /api/analysis/report-pack
```

示例请求：

```json
{
  "platform": "zhilian",
  "report_type": "daily",
  "date_basis": "delivery",
  "date_from": "2026-03-25",
  "date_to": "2026-03-25",
  "account_ids_csv": "3",
  "sample_limit": 5,
  "max_description_chars": 240,
  "focus": "请重点关注高价值方向、失败原因和明日建议"
}
```

你也可以直接用 `curl` 先验接口：

```bash
curl -X POST "http://127.0.0.1:8000/api/analysis/report-pack" ^
  -H "Content-Type: application/json" ^
  -d "{\"platform\":\"zhilian\",\"report_type\":\"daily\",\"date_basis\":\"delivery\",\"date_from\":\"2026-03-25\",\"date_to\":\"2026-03-25\",\"account_ids_csv\":\"3\",\"sample_limit\":5}"
```

## 启动方式

```bash
cd G:\CodeProject\Python\job_help_api
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 导入到 Dify

1. 在 Dify 新建 `Workflow` 应用
2. 导入 [dify_job_delivery_daily.workflow.yml](/G:/CodeProject/Python/job_help_api/md/dify_job_delivery_daily.workflow.yml)
3. 打开 `获取分析数据包` 节点，确认 URL 是否可访问
4. 打开 `生成日报` 节点，重新选择你自己 Dify 里可用的模型

## URL 说明

工作流里的默认地址是：

```text
http://host.docker.internal:8000/api/analysis/report-pack
```

适用于：

- Dify 运行在 Docker
- `job_help_api` 运行在宿主机

如果你的 Dify 不是 Docker 部署，请按实际情况改成：

- `http://127.0.0.1:8000/api/analysis/report-pack`
- 或你自己的局域网 IP

## 推荐测试顺序

建议先测这组真实数据：

- `platform=zhilian`
- `date_basis=delivery`
- `date_from=2026-03-25`
- `date_to=2026-03-25`
- `account_ids_csv=3`

这组数据在当前库里同时包含：

- `delivered`
- `failed`
- `skipped`

比较适合先验证日报质量。

## 当前接口返回内容

`report-pack` 里已经包含：

- `meta`
- `field_flags`
- `stats`
- `samples`
- `data_quality_flags`
- `notes`

其中 `stats` 里有：

- 状态分布
- 城市分布
- 经验/学历分布
- 技能关键词热度
- 公司频次
- 账号表现
- 跳过原因 / 失败原因
- 月薪 / 日薪分桶

`samples` 里有：

- `recent_jobs`
- `delivered_jobs`
- `failed_jobs`
- `skipped_jobs`
- `high_monthly_salary_jobs`
- `high_daily_salary_jobs`
- `tag_dense_jobs`

## 已处理的两个风险

1. 应用默认不再自动写入 demo 数据  
现在通过 `bootstrap_demo_data_enabled=false` 控制，避免启动时污染分析结果。

2. 月薪和日薪分开统计  
接口会输出 `salary_unit_buckets`、`monthly_salary_buckets`、`daily_salary_buckets`，并在混用时加 `mixed_salary_units` 提示。

## 还需要你注意

当前数据库里已经存在早先写入的 demo 记录和真实记录混合的情况。默认新代码不会继续自动写入，但旧 demo 数据如果要彻底清掉，建议你确认后再单独清理。
