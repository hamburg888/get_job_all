import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from app.automation.browser_profiles import ensure_profile_dir
from app.core.config import settings
from app.models.entities import Platform

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ZhilianSelectors:
    login_link: str = "a.header-nav__c-no-login"
    keyword_input: str = 'input.query-search__content-input[placeholder="输入职位、公司等搜索"]'
    search_button: str = "button.query-search__content-button"
    job_list: str = "div.positionlist__list"
    job_card: str = "div.joblist-box__item.clearfix"
    salary: str = "span.summary-plane__salary"
    title: str = "h1.summary-plane__title"
    description: str = "div.describtion__detail-content"
    company: str = "a.company__title"
    tag_items: str = "div.describtion__skills span.describtion__skills-item"
    deliver_button: str = 'button.a-button.a--bordered.a--filled:has-text("立即投递")'
    delivered_button: str = 'button.a-button.a--bordered.a--filled.a--disabled:has-text("已投递")'
    success_dialog_main: str = "span.deliver-title__left__word"
    success_dialog_secondary: str = "span.deliver-dialog__box__title"
    next_page: str = 'a.btn.soupager__btn:has-text("下一页")'


class ZhilianAdapter:
    def __init__(self) -> None:
        self.selectors = ZhilianSelectors()

    @staticmethod
    def extract_job_code(detail_url: str) -> str:
        match = re.search(r"/jobdetail/([A-Z0-9]+)\.htm", detail_url)
        return match.group(1) if match else urlparse(detail_url).path.rsplit("/", maxsplit=1)[-1]

    @staticmethod
    def normalize_salary(raw_salary: str) -> tuple[float | None, float | None]:
        salary = raw_salary.replace("元", "").replace("万", "w").strip()
        if "-" not in salary:
            return None, None
        left, right = salary.split("-", maxsplit=1)

        def to_k(value: str) -> float | None:
            digits = re.findall(r"\d+(?:\.\d+)?", value)
            if not digits:
                return None
            number = float(digits[0])
            if "w" in value.lower():
                return number * 10
            return number / 1000 if number > 1000 else number

        return to_k(left), to_k(right)

    async def run_search_flow(
        self,
        keyword: str,
        per_job_callback: Callable[[dict], None],
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
    ) -> None:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(ensure_profile_dir(Platform.ZHILIAN)),
                headless=settings.browser_headless,
            )
            list_page = context.pages[0] if context.pages else await context.new_page()
            await list_page.goto(settings.zhilian_entry_url, wait_until="domcontentloaded")
            if await self.is_login_required(list_page):
                if status_callback:
                    status_callback("智联招聘当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                await self.wait_for_login(list_page, should_stop, status_callback, confirm_login_callback)
                if should_stop():
                    await context.close()
                    return
            await list_page.locator(self.selectors.keyword_input).fill(keyword)
            await list_page.locator(self.selectors.search_button).click()
            await list_page.wait_for_timeout(10_000)

            while not should_stop():
                cards = list_page.locator(self.selectors.job_card)
                count = await cards.count()
                if count == 0:
                    logger.warning("No job cards found on current page.")
                    if status_callback:
                        status_callback("当前页未找到岗位卡片，任务结束。")
                    break
                # Temporary test mode: only process the first job on each page,
                # then jump to the next page to verify pagination flow.
                for index in range(min(count, 1)):
                    if should_stop():
                        break
                    if status_callback:
                        status_callback(f"正在打开第 {index + 1} 个岗位详情")
                    async with context.expect_page() as page_info:
                        await cards.nth(index).click()
                    detail_page = await page_info.value
                    await detail_page.wait_for_load_state("domcontentloaded")
                    detail_url = detail_page.url
                    job_payload = await self.extract_job_payload(detail_page, detail_url)
                    try:
                        await detail_page.locator(self.selectors.deliver_button).click(timeout=5000)
                        await detail_page.wait_for_timeout(2000)
                        delivery_result = await self.detect_delivery_success(context, detail_page)
                        job_payload["delivery_success"] = delivery_result["success"]
                        job_payload["status_reason"] = delivery_result["reason"]
                    except PlaywrightTimeoutError:
                        logger.info("Deliver button not clickable for %s", detail_url)
                        job_payload["delivery_success"] = False
                        job_payload["status_reason"] = "未找到可点击的立即投递按钮"
                    finally:
                        per_job_callback(job_payload)
                        await self.close_extra_tabs(context, list_page)

                next_page = list_page.locator(self.selectors.next_page)
                if await next_page.count() == 0:
                    break
                if status_callback:
                    status_callback("当前页处理完成，准备进入下一页")
                await next_page.click()
                await list_page.wait_for_timeout(4000)

            await context.close()

    async def is_login_required(self, page) -> bool:
        login_link = page.locator(self.selectors.login_link)
        if await login_link.count() == 0:
            return False
        href = await login_link.first.get_attribute("href")
        return bool(href and "passport.zhaopin.com/login" in href)

    async def detect_delivery_success(self, context, detail_page) -> dict:
        for page in context.pages:
            main_success = page.locator(self.selectors.success_dialog_main)
            if await main_success.count() and "投递成功" in await main_success.first.inner_text():
                return {"success": True, "reason": "检测到投递成功弹窗"}

            secondary_success = page.locator(self.selectors.success_dialog_secondary)
            if await secondary_success.count() and "投递成功" in await secondary_success.first.inner_text():
                return {"success": True, "reason": "检测到投递成功对话框"}

        delivered_button = detail_page.locator(self.selectors.delivered_button)
        if await delivered_button.count():
            return {"success": True, "reason": "投递按钮已变更为已投递"}

        return {"success": False, "reason": "未检测到投递成功标识"}

    async def wait_for_login(
        self,
        page,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
    ) -> None:
        while not should_stop():
            if confirm_login_callback and confirm_login_callback():
                if await self.is_login_required(page):
                    if status_callback:
                        status_callback("仍检测到未登录，请先完成智联登录后再次确认。")
                else:
                    if status_callback:
                        status_callback("已确认登录完成，开始执行搜索与投递。")
                    return
            await page.wait_for_timeout(2000)

    async def close_extra_tabs(self, context, list_page) -> None:
        for page in list(context.pages):
            if page != list_page:
                await page.close()

    async def extract_job_payload(self, detail_page, detail_url: str) -> dict:
        salary_text = await detail_page.locator(self.selectors.salary).inner_text()
        title = await detail_page.locator(self.selectors.title).inner_text()
        description = await detail_page.locator(self.selectors.description).inner_text()
        company_name = await detail_page.locator(self.selectors.company).inner_text()
        tags = await detail_page.locator(self.selectors.tag_items).all_inner_texts()
        salary_min, salary_max = self.normalize_salary(salary_text)
        return {
            "platform_job_id": self.extract_job_code(detail_url),
            "title": title.strip(),
            "company_name": company_name.strip(),
            "salary_text": salary_text.strip(),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "description": description.strip(),
            "detail_url": detail_url,
            "tags": [tag.strip() for tag in tags if tag.strip()],
        }

    def run_sync_mock(self, keyword: str, per_job_callback: Callable[[dict], None], should_stop: Callable[[], bool]) -> None:
        samples = [
            {
                "platform_job_id": "CC302351080J40924781315",
                "title": "软件开发（无经验考研考公断档，不限语言）",
                "company_name": "广东上药桑尼克医疗科技有限公司",
                "salary_text": "1.5-3万",
                "salary_min": 15,
                "salary_max": 30,
                "description": "在指导下参与软件模块设计和维护。",
                "detail_url": "https://www.zhaopin.com/jobdetail/CC302351080J40924781315.htm",
                "tags": ["JavaScript", "CSS", "Spring"],
                "city": "广州",
                "experience_text": "不限",
                "delivery_success": True,
                "status_reason": f"关键词 {keyword} 命中，已完成模拟投递。",
            },
            {
                "platform_job_id": "CC102937182J40281766609",
                "title": "全栈工程师",
                "company_name": "柏川科技",
                "salary_text": "8000-11000元",
                "salary_min": 8,
                "salary_max": 11,
                "description": "负责业务系统前后端协同开发。",
                "detail_url": "https://www.zhaopin.com/jobdetail/CC102937182J40281766609.htm",
                "tags": ["Vue3", "Python", "PostgreSQL"],
                "city": "广州",
                "experience_text": "1-3年",
                "delivery_success": True,
                "status_reason": f"关键词 {keyword} 命中，职位满足薪资与城市条件。",
            },
        ]
        for sample in samples:
            if should_stop():
                break
            per_job_callback(sample)
            asyncio.run(asyncio.sleep(1.2))
