from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.entities import Account, DeliveryRecord, DeliveryStatus, Job, Platform, RuleConfig, TaskLog, TaskRun, TaskStatus


def bootstrap_demo_data() -> None:
    with SessionLocal() as db:
        has_account = db.scalar(select(Account.id).limit(1))
        if not has_account:
            accounts = [
                Account(
                    platform=Platform.ZHILIAN.value,
                    phone="13800000001",
                    username="智联账号A",
                    remark="默认智联账号",
                    profile_dir="browser_profiles/zhilian/account_1",
                    is_default=True,
                ),
                Account(
                    platform=Platform.BOSS.value,
                    phone="13900000001",
                    username="Boss账号A",
                    remark="默认Boss账号",
                    profile_dir="browser_profiles/boss/account_2",
                    is_default=True,
                ),
            ]
            db.add_all(accounts)
            db.commit()

        has_rule = db.scalar(select(RuleConfig.id).limit(1))
        if has_rule:
            return

        rules = [
            RuleConfig(platform=Platform.ZHILIAN.value, keyword="全栈开发", city="广州", auto_send_chat=False),
            RuleConfig(platform=Platform.BOSS.value, keyword="Python后端", city="深圳", auto_send_chat=True),
        ]
        db.add_all(rules)
        db.flush()

        task = TaskRun(
            platform=Platform.BOSS.value,
            keyword="Python后端",
            status=TaskStatus.COMPLETED.value,
            current_step="示例任务已完成",
            processed_count=3,
            delivered_count=2,
            failed_count=1,
        )
        db.add(task)
        db.flush()

        jobs = [
            Job(
                platform=Platform.BOSS.value,
                platform_job_id="boss-demo-1",
                title="Python后端开发",
                company_name="星河数据",
                salary_text="20-30K",
                salary_min=20,
                salary_max=30,
                city="深圳",
                experience_text="3-5年",
                detail_url="https://www.zhipin.com/job_detail/demo-1.html",
                tags=["Python", "FastAPI", "Redis"],
                latest_status=DeliveryStatus.DELIVERED.value,
                task_run_id=task.id,
            ),
            Job(
                platform=Platform.BOSS.value,
                platform_job_id="boss-demo-2",
                title="平台研发工程师",
                company_name="南岸智算",
                salary_text="25-40K",
                salary_min=25,
                salary_max=40,
                city="深圳",
                experience_text="5-10年",
                detail_url="https://www.zhipin.com/job_detail/demo-2.html",
                tags=["Go", "云原生"],
                latest_status=DeliveryStatus.FAILED.value,
                task_run_id=task.id,
            ),
        ]
        db.add_all(jobs)
        db.flush()

        deliveries = [
            DeliveryRecord(
                job_id=jobs[0].id,
                task_run_id=task.id,
                platform=Platform.BOSS.value,
                status=DeliveryStatus.DELIVERED.value,
                status_reason="已投递，自动发送问候语成功",
                chat_sent=True,
                chat_message="您好，我刚刚投递了贵司的 Python后端开发 岗位。",
            ),
            DeliveryRecord(
                job_id=jobs[1].id,
                task_run_id=task.id,
                platform=Platform.BOSS.value,
                status=DeliveryStatus.FAILED.value,
                status_reason="岗位已关闭",
                chat_sent=False,
            ),
        ]
        db.add_all(deliveries)
        db.add(
            TaskLog(
                platform=Platform.BOSS.value,
                task_run_id=task.id,
                level="INFO",
                event_type="seed",
                message="已写入示例 Boss 数据，便于前端先联调。",
            )
        )
        db.commit()
