import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.config import settings
from app.automation.browser_factory import (
    LAODENG_INIT_SCRIPT,
    STEALTH_INIT_SCRIPT,
    build_browser_launch_options,
)
from app.services.live_preview_service import live_preview_service

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
    action_container: str = "div.summary-plane__action"
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
        search_terms: list[str],
        profile_dir: str,
        per_job_callback: Callable[[dict], None],
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
        *,
        entry_url: str | None = None,
        task_run_id: int | None = None,
        headless: bool | None = None,
        live_preview_enabled: bool = False,
        live_preview_interval_ms: int = 1000,
        delivery_interval_seconds: int = 3,
        max_deliveries_per_run: int = 100,
    ) -> None:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        launch_headless = settings.browser_headless if headless is None else headless
        target_entry_url = (entry_url or settings.zhilian_entry_url).strip() or settings.zhilian_entry_url
        use_manual_entry_url = bool(entry_url and entry_url.strip())
        effective_search_terms = search_terms if search_terms else [""]
        async with async_playwright() as playwright:
            launch_options = build_browser_launch_options(launch_headless)
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                **launch_options,
            )
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.add_init_script(LAODENG_INIT_SCRIPT)
            list_page = context.pages[0] if context.pages else await context.new_page()
            preview_ref = {"page": list_page}
            preview_task = None
            if launch_headless and live_preview_enabled and task_run_id is not None:
                preview_task = asyncio.create_task(
                    self._preview_loop(
                        context,
                        preview_ref,
                        should_stop,
                        task_run_id,
                        live_preview_interval_ms,
                    )
                )
            await list_page.goto(target_entry_url, wait_until="domcontentloaded")
            try:
                if await self.is_login_required(list_page):
                    if status_callback:
                        status_callback("智联招聘当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                    await self.wait_for_login(list_page, should_stop, status_callback, confirm_login_callback)
                    if should_stop():
                        return
                for search_term in effective_search_terms:
                    if should_stop():
                        break
                    if status_callback and use_manual_entry_url:
                        status_callback("正在打开手动配置的筛选 URL")
                    elif status_callback:
                        status_callback(f"正在搜索关键词: {search_term}")
                    preview_ref["page"] = list_page
                    await list_page.goto(target_entry_url, wait_until="domcontentloaded")
                    if use_manual_entry_url:
                        await list_page.wait_for_timeout(6000)
                    else:
                        await list_page.locator(self.selectors.keyword_input).fill("")
                        await list_page.locator(self.selectors.keyword_input).fill(search_term)
                        await list_page.locator(self.selectors.search_button).click()
                        await list_page.wait_for_timeout(10_000)

                    while not should_stop():
                        cards = list_page.locator(self.selectors.job_card)
                        count = await cards.count()
                        if count == 0:
                            logger.warning("No job cards found on current page.")
                            if status_callback:
                                status_callback(f"搜索词 {search_term} 当前页未找到岗位卡片，切换下一个搜索词。")
                            break
                        for index in range(count):
                            if should_stop():
                                break
                            if status_callback:
                                status_callback(f"搜索词 {search_term}：正在打开第 {index + 1} 个岗位详情")
                            async with context.expect_page() as page_info:
                                await cards.nth(index).click()
                            detail_page = await page_info.value
                            preview_ref["page"] = detail_page
                            await detail_page.wait_for_load_state("domcontentloaded")
                            await detail_page.wait_for_timeout(2500)
                            detail_url = detail_page.url
                            job_payload = await self.extract_job_payload(detail_page, detail_url)
                            job_payload["search_term"] = search_term or "手动URL"
                            try:
                                delivery_result = await self.try_deliver(detail_page, context)
                                await detail_page.wait_for_timeout(5000)
                                detect_result = await self.detect_delivery_success(context, detail_page)
                                job_payload["delivery_success"] = detect_result["success"]
                                job_payload["status_reason"] = self.merge_reasons(
                                    delivery_result["reason"],
                                    detect_result["reason"],
                                )
                            except PlaywrightTimeoutError:
                                logger.info("Deliver button not clickable for %s", detail_url)
                                job_payload["delivery_success"] = False
                                job_payload["status_reason"] = "未找到可点击的立即投递按钮"
                            finally:
                                per_job_callback(job_payload)
                                await self.close_extra_tabs(context, list_page)
                                preview_ref["page"] = list_page

                        next_page = list_page.locator(self.selectors.next_page)
                        if await next_page.count() == 0:
                            break
                        if status_callback:
                            status_callback(f"搜索词 {search_term} 当前页处理完成，准备进入下一页")
                        await next_page.click()
                        await list_page.wait_for_timeout(4000)
            finally:
                if preview_task:
                    preview_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await preview_task
                await context.close()

    async def _preview_loop(
        self,
        context,
        preview_ref: dict[str, object],
        should_stop: Callable[[], bool],
        task_run_id: int,
        live_preview_interval_ms: int,
    ) -> None:
        interval = max(500, live_preview_interval_ms) / 1000
        while not should_stop():
            page = preview_ref.get("page")
            if (page is None or page.is_closed()) and context.pages:
                page = context.pages[0]
                preview_ref["page"] = page
            if page is not None and not page.is_closed():
                with contextlib.suppress(Exception):
                    await live_preview_service.capture_page("zhilian", task_run_id, page)
            await asyncio.sleep(interval)

    async def is_login_required(self, page) -> bool:
        login_link = page.locator(self.selectors.login_link)
        if await login_link.count() == 0:
            return False
        href = await login_link.first.get_attribute("href")
        return bool(href and "passport.zhaopin.com/login" in href)

    async def detect_delivery_success(self, context, detail_page) -> dict:
        delivered_button = detail_page.locator(self.selectors.delivered_button)
        if await delivered_button.count():
            return {"success": True, "reason": "成功检测：投递按钮已变更为已投递"}

        for page in context.pages:
            main_success = page.locator(self.selectors.success_dialog_main)
            if await main_success.count() and "投递成功" in await main_success.first.inner_text():
                return {"success": True, "reason": "成功检测：检测到投递成功弹窗"}

            secondary_success = page.locator(self.selectors.success_dialog_secondary)
            if await secondary_success.count() and "投递成功" in await secondary_success.first.inner_text():
                return {"success": True, "reason": "成功检测：检测到投递成功对话框"}

        button_snapshot = await self.inspect_deliver_button(detail_page)
        return {"success": False, "reason": f"未检测到投递成功标识 | {button_snapshot}"}

    async def try_deliver(self, detail_page, context) -> dict:
        delivered_button = detail_page.locator(self.selectors.delivered_button)
        if await delivered_button.count():
            return {"success": True, "reason": "点击前检测：进入详情时岗位已是已投递状态"}

        await detail_page.bring_to_front()
        action_container = detail_page.locator(self.selectors.action_container)
        if await action_container.count():
            await action_container.first.scroll_into_view_if_needed()
        await detail_page.wait_for_timeout(1200)

        button = detail_page.locator(self.selectors.deliver_button).first
        before_snapshot = await self.inspect_deliver_button(detail_page)
        await button.wait_for(state="visible", timeout=8000)
        await button.scroll_into_view_if_needed()
        await button.hover()

        try:
            await button.click(timeout=5000)
            result = await self.evaluate_click_attempt(detail_page, context, before_snapshot, "普通点击")
            if result is not None:
                return result
        except Exception:  # noqa: BLE001
            logger.warning("Normal deliver click failed.", exc_info=True)

        final_snapshot = await self.inspect_deliver_button(detail_page)
        return {
            "success": False,
            "reason": (
                f"单次点击未触发状态变化 | 点击前：{before_snapshot} | 点击后：{final_snapshot}"
            ),
        }

    async def evaluate_click_attempt(self, detail_page, context, before_snapshot: str, click_mode: str) -> dict | None:
        await detail_page.wait_for_timeout(1200)
        detect_result = await self.detect_delivery_success(context, detail_page)
        after_snapshot = await self.inspect_deliver_button(detail_page)
        if detect_result["success"] or after_snapshot != before_snapshot:
            return {
                "success": True,
                "reason": (
                    f"点击方式：{click_mode} | 点击前：{before_snapshot} | 点击后：{after_snapshot} | {detect_result['reason']}"
                ),
            }
        return None

    async def inspect_deliver_button(self, detail_page) -> str:
        deliver_buttons = detail_page.locator(self.selectors.deliver_button)
        deliver_count = await deliver_buttons.count()
        delivered_buttons = detail_page.locator(self.selectors.delivered_button)
        delivered_count = await delivered_buttons.count()
        action_container = detail_page.locator(self.selectors.action_container)
        action_count = await action_container.count()

        if deliver_count:
            button = deliver_buttons.first
            try:
                text = (await button.inner_text()).strip()
            except Exception:  # noqa: BLE001
                text = "<unreadable>"
            try:
                disabled = await button.is_disabled()
            except Exception:  # noqa: BLE001
                disabled = False
            return (
                f"action_count={action_count}, deliver_count={deliver_count}, delivered_count={delivered_count}, "
                f"text={text}, disabled={disabled}"
            )

        return (
            f"action_count={action_count}, deliver_count={deliver_count}, delivered_count={delivered_count}, "
            "text=<missing>, disabled=<unknown>"
        )

    @staticmethod
    def merge_reasons(click_reason: str, detect_reason: str) -> str:
        return f"{click_reason} | {detect_reason}"

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
