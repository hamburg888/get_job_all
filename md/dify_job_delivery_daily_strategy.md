# Dify 复杂版工作流说明

## 这版和上一版的区别

这版不再只生成日报，而是一次输出两份内容：

- `daily_report_markdown`
- `strategy_memo_markdown`

同时它会利用后端新增的这些数据：

- `comparison`
- `strategy_inputs`
- `data_quality_flags`
- `stats.daily_trends`

## 适合什么场景

适合你现在这种“先稳定跑通，再逐步升级”的阶段：

- 白天看日报
- 晚上做投递策略复盘
- 或者按账号 / 平台对比哪个方向该放大、哪个该收缩

## 工作流文件

导入这份：

[dify_job_delivery_daily_strategy.workflow.yml](/G:/CodeProject/Python/job_help_api/md/dify_job_delivery_daily_strategy.workflow.yml)

## 节点结构

复杂版工作流目前是：

1. 开始
2. 获取复杂分析数据包
3. 生成日报
4. 生成策略建议
5. 结束

这样做的好处是：

- 日报和策略建议分开建模，输出更稳
- 策略节点可以直接参考日报文本，减少重复分析
- 后端先把异常和冗错信号算出来，LLM 不需要自己猜

## 复杂版接口能力

`POST /api/analysis/report-pack` 现在除了原来的 `meta/stats/samples`，还新增了：

- `comparison`
  - 对比上一等长时间窗口
  - 给出 `job_count / event_count / delivered_count / failed_count / skipped_count / delivery_success_rate` 的变化
  - 给出 `top_rising_tags / top_falling_tags`

- `strategy_inputs`
  - `focus_directions`
  - `caution_directions`
  - `keyword_opportunities`
  - `city_opportunities`
  - `high_value_delivered_jobs`
  - `high_value_skipped_jobs`

- `data_quality_flags`
  - `mixed_salary_units`
  - `suspicious_salary_values`
  - `description_truncated`
  - `previous_period_empty`
  - `unknown_account_ids`
  - `wide_date_window`

## 冗错设计

目前这版的冗错主要放在后端数据包层：

1. 账号 ID 不存在时，不直接报错中断，而是继续用能识别到的账号生成报告，并打 `unknown_account_ids`
2. 上一个对比周期没有数据时，不中断，而是打 `previous_period_empty`
3. 月薪和日薪混杂时，打 `mixed_salary_units`
4. 可疑薪资值时，打 `suspicious_salary_values`
5. 描述过长被截断时，打 `description_truncated`
6. 窗口过长时，打 `wide_date_window`

## 你导入后需要手动改的地方

1. `获取复杂分析数据包` 节点的 URL  
默认是：

```text
http://host.docker.internal:8000/api/analysis/report-pack
```

如果你的 Dify 不是 Docker，改成：

```text
http://127.0.0.1:8000/api/analysis/report-pack
```

2. 两个 LLM 节点的模型  
把 `gpt-4o-mini` 换成你 Dify 里已经配置好的模型。

## 建议你怎么测

先测这两组：

### 组 1：智联生产账号

- `platform=zhilian`
- `date_basis=delivery`
- `date_from=2026-03-25`
- `date_to=2026-03-25`
- `account_ids_csv=3`

这组适合看：

- 失败原因
- 投递成功率
- 策略建议质量

### 组 2：Boss 生产账号

- `platform=boss`
- `date_basis=delivery`
- `date_from=2026-03-25`
- `date_to=2026-03-25`
- `account_ids_csv=4`

这组适合看：

- 高薪机会
- 薪资异常值
- 去重 / 薪资阈值导致的跳过原因

## 下一步还可以怎么升

如果你后面还想继续升，我建议优先做这 3 件事：

1. 再拆一个“Reviewer”阶段  
让第三个 LLM 专门检查日报和策略里有没有无依据结论。

2. 做“空结果兜底分支”  
当 `empty_result` 出现时，直接输出固定模板，而不是继续走完整分析。

3. 加“多日趋势周报”  
把窗口扩展到最近 7 天或 14 天，重点分析标签变化和账号表现变化。
