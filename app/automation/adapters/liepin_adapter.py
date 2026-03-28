import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.automation.adapters.qiancheng_adapter import QianchengAdapter
from app.automation.browser_factory import (
    LAODENG_INIT_SCRIPT,
    STEALTH_INIT_SCRIPT,
    build_browser_launch_options,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LiepinSelectors:
    search_input: str = 'input[placeholder="搜索职位、公司"]'
    job_card: str = "div.job-card-pc-container"
    detail_link: str = 'a[href*="/lptjob/"]'
    active_page: str = ".ant-pagination-item-active"
    next_page: str = ".ant-pagination-next"
    description: str = ".job-intro-container [data-selector='job-intro-content'], .job-intro-container"
    recruiter_container: str = ".recruiter-container"
    company_info: str = ".company-info-container"
    apply_button_candidates: tuple[str, ...] = (
        'a.btn-minor[data-selector="apply-job"]',
        'a[data-selector="apply-job"]',
        'a:has-text("投简历")',
    )
    apply_resume_modal: str = '.ant-modal-content:has-text("选择附件简历")'
    apply_confirm_button_candidates: tuple[str, ...] = (
        '.ant-modal-footer button.ant-btn.ant-btn-primary:has-text("立即投递")',
        '.ant-modal-footer button:has-text("立即投递")',
        'button.ant-btn.ant-btn-primary:has-text("立即投递")',
    )
    apply_success_candidates: tuple[str, ...] = (
        'text=投递成功',
        '._40106YmCap',
        'input[value="一键发简历"]',
    )
    chat_button_candidates: tuple[str, ...] = (
        'a.btn-main[data-selector="chat-chat"]',
        'a.btn-chat[data-selector="chat-chat"]',
        'a:has-text("继续聊")',
    )
    chat_root_candidates: tuple[str, ...] = (
        "#im-c-entry",
        ".im-ui-chat-modal-container",
        ".ant-im-modal-wrap.im-ui-basic-chat-wrapper",
        '.ant-modal:has-text("我的沟通")',
    )
    chat_textarea_candidates: tuple[str, ...] = (
        "textarea.ant-im-input.ant-im-input-borderless.im-ui-textarea",
        "textarea.ant-input",
        "textarea",
    )
    chat_send_button_candidates: tuple[str, ...] = (
        "button.ant-im-btn.ant-im-btn-primary.im-ui-basic-send-btn.ant-im-teno-btn",
        'button.ant-btn-primary:has-text("发送")',
        'button:has-text("发送")',
    )
    chat_image_input_candidates: tuple[str, ...] = (
        '.im-ui-upload-container input[type="file"]',
        'input[type="file"][accept*="png"]',
        'input[type="file"]',
    )
    chat_image_confirm_modal: str = ".ant-im-modal-content"
    chat_image_confirm_button_candidates: tuple[str, ...] = (
        ".ant-im-modal-footer .ant-im-btn.ant-im-btn-primary",
        '.ant-im-modal-footer button:has-text("确定")',
        'button.ant-im-btn.ant-im-btn-primary:has-text("确定")',
    )
    chat_image_message_candidates: tuple[str, ...] = (
        ".im-ui-img-message img",
        ".im-ui-img-message .rc-image-img",
    )
    chat_close_candidates: tuple[str, ...] = (
        "button.ant-im-drawer-close",
        '[aria-label="close"]',
        '[aria-label="Close"]',
        ".anticon-close",
        '.ant-modal-close',
    )


class LiepinAdapter(QianchengAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.selectors = LiepinSelectors()
        self._auto_send_chat = False
        self._use_chat_template = False
        self._chat_message_template = ""
        self._enable_image_resume = False
        self._image_resume_path = ""

    @staticmethod
    def extract_job_code(detail_url: str) -> str:
        if not detail_url:
            return ""
        match = re.search(r"/lptjob/(\d+)", detail_url)
        if match:
            return match.group(1)
        return QianchengAdapter.extract_job_code(detail_url)

    @staticmethod
    def normalize_salary(raw_salary: str) -> tuple[float | None, float | None]:
        return QianchengAdapter.normalize_salary(raw_salary)

    def _chat_actions_enabled(self) -> bool:
        return self._auto_send_chat or self._enable_image_resume

    def _default_send_status(self) -> str:
        return "failed" if self._chat_actions_enabled() else "skipped"

    @staticmethod
    def _combine_delivery_reason(base_reason: str, chat_summary: str) -> str:
        normalized_base = (base_reason or "").strip()
        normalized_summary = (chat_summary or "").strip()
        if normalized_base and normalized_summary:
            return f"{normalized_base} | {normalized_summary}"
        return normalized_base or normalized_summary

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
        auto_send_chat: bool = False,
        use_chat_template: bool = False,
        chat_message_template: str = "",
        enable_image_resume: bool = False,
        image_resume_path: str = "",
    ) -> None:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        del max_deliveries_per_run

        self._auto_send_chat = auto_send_chat
        self._use_chat_template = use_chat_template
        self._chat_message_template = chat_message_template or ""
        self._enable_image_resume = enable_image_resume
        self._image_resume_path = image_resume_path or ""

        launch_headless = settings.browser_headless if headless is None else headless
        use_persistent_context = bool(launch_headless and not settings.browser_fake_headless)
        target_entry_url = (entry_url or settings.liepin_entry_url).strip() or settings.liepin_entry_url
        effective_search_terms = search_terms if search_terms else [""]

        async with async_playwright() as playwright:
            browser = None
            chrome_pid: int | None = None
            if use_persistent_context:
                launch_options = build_browser_launch_options(launch_headless)
                context = await playwright.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    **launch_options,
                )
            else:
                chrome_path = self._resolve_chrome_executable()
                profile_path = Path(profile_dir)
                if not profile_path.is_absolute():
                    profile_path = Path.cwd() / profile_path
                profile_path.mkdir(parents=True, exist_ok=True)

                debug_port = self._pick_free_port()
                chrome_pid = self._launch_external_chrome(
                    chrome_path,
                    profile_path,
                    debug_port,
                    target_entry_url,
                    launch_options=build_browser_launch_options(launch_headless),
                )
                self._bind_runtime(asyncio.get_running_loop(), None, None, chrome_pid)
                ws_endpoint = self._wait_for_cdp_endpoint(debug_port, timeout=20, should_stop=should_stop)
                if not ws_endpoint:
                    raise RuntimeError("猎聘外部 Chrome 已启动，但未能连接到调试端口。")
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            self._bind_runtime(asyncio.get_running_loop(), context, browser, chrome_pid)
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

            try:
                await list_page.goto(target_entry_url, wait_until="domcontentloaded")
                if await self.is_login_required(list_page):
                    if status_callback:
                        status_callback("猎聘当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                    await self.wait_for_login(list_page, should_stop, status_callback, confirm_login_callback)
                    if should_stop():
                        return

                for search_term in effective_search_terms:
                    if should_stop():
                        break

                    search_label = (search_term or "").strip() or "默认搜索页"
                    preview_ref["page"] = list_page
                    list_page = await self.prepare_search_results_page(
                        list_page,
                        target_entry_url,
                        search_term,
                        should_stop,
                        status_callback,
                    )
                    if await self.is_login_required(list_page):
                        if status_callback:
                            status_callback("猎聘当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                        await self.wait_for_login(list_page, should_stop, status_callback, confirm_login_callback)
                        if should_stop():
                            return
                        list_page = await self.prepare_search_results_page(
                            list_page,
                            target_entry_url,
                            search_term,
                            should_stop,
                            status_callback,
                        )

                    while not should_stop():
                        list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                        cards = list_page.locator(self.selectors.job_card)
                        count = await cards.count()
                        if count == 0:
                            if status_callback:
                                status_callback(f"{search_label} 当前页未找到岗位卡片，切换下一组条件。")
                            break

                        for index in range(count):
                            if should_stop():
                                break

                            if status_callback:
                                status_callback(f"{search_label}：正在打开第 {index + 1} 个岗位详情")

                            list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                            cards = list_page.locator(self.selectors.job_card)
                            if index >= await cards.count():
                                break

                            list_card_payload = await self.extract_list_card_payload(cards.nth(index), self._safe_page_url(list_page))
                            pages_before_detail = self._snapshot_open_pages(context)
                            detail_page = await self.open_detail_page(context, list_page, cards.nth(index))
                            preview_ref["page"] = detail_page
                            stop_after_cleanup = False
                            job_payload = None

                            try:
                                await detail_page.wait_for_load_state("domcontentloaded")
                                if await self._sleep_or_stop(1.0, should_stop):
                                    stop_after_cleanup = True
                                    continue

                                detail_url = self._safe_page_url(detail_page)
                                detail_payload = await self.extract_job_payload(detail_page, detail_url)
                                job_payload = {**list_card_payload, **detail_payload}
                                job_payload["search_term"] = search_label
                                job_payload["already_delivered"] = False
                                delivery_result = await self.try_deliver(detail_page, should_stop)
                                job_payload["already_delivered"] = bool(delivery_result.get("already_delivered", False))
                                job_payload["delivery_success"] = bool(delivery_result.get("success", False))
                                job_payload["status_reason"] = str(delivery_result.get("reason", "未完成投递"))
                                job_payload["chat_sent"] = bool(delivery_result.get("chat_sent", False))
                                job_payload["chat_message"] = str(delivery_result.get("chat_message", ""))
                                job_payload["send_status"] = str(delivery_result.get("send_status", ""))
                            except (PlaywrightTimeoutError, TimeoutError):
                                logger.info("Liepin detail flow timed out for %s", list_card_payload.get("detail_url", ""))
                                if job_payload is None:
                                    job_payload = dict(list_card_payload)
                                    job_payload["search_term"] = search_label
                                job_payload["delivery_success"] = False
                                job_payload["status_reason"] = "猎聘职位详情处理超时"
                                job_payload["chat_sent"] = False
                                job_payload["chat_message"] = ""
                                job_payload["send_status"] = "skipped"
                            finally:
                                if job_payload is not None:
                                    per_job_callback(job_payload)
                                list_page = await self.close_delivery_pages(
                                    context,
                                    list_page,
                                    detail_page,
                                    pages_before_detail,
                                    target_entry_url,
                                    should_stop,
                                )
                                with contextlib.suppress(Exception):
                                    await list_page.bring_to_front()
                                preview_ref["page"] = list_page

                            if stop_after_cleanup:
                                break

                            if delivery_interval_seconds > 0 and await self._sleep_or_stop(float(delivery_interval_seconds), should_stop):
                                break

                        next_page = list_page.locator(self.selectors.next_page).first
                        if await next_page.count() == 0:
                            break
                        disabled = (await next_page.get_attribute("disabled")) or ""
                        class_name = (await next_page.get_attribute("class")) or ""
                        if disabled or "disabled" in class_name.lower():
                            break

                        if status_callback:
                            current_page_number = await self._read_active_page_index(list_page) or 1
                            status_callback(f"{search_label} 当前页处理完成，准备进入第 {current_page_number + 1} 页")

                        await self.go_to_next_page(list_page, should_stop, status_callback)
                        if await self._sleep_or_stop(1.5, should_stop):
                            break
            finally:
                if preview_task:
                    preview_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await preview_task
                with contextlib.suppress(Exception):
                    await context.close()
                if browser is not None:
                    with contextlib.suppress(Exception):
                        await browser.close()
                self._clear_runtime()
                self._terminate_chrome_process(chrome_pid)

    async def is_login_required(self, page) -> bool:
        current_url = self._safe_page_url(page).lower()
        if self._is_login_redirect(current_url):
            return True
        with contextlib.suppress(Exception):
            if await page.locator("text=你好，").count():
                return False
        login_selectors = (
            'a[href*="passport.liepin.com"]',
            'a[href*="/login"]',
            'button:has-text("登录")',
            'a:has-text("登录")',
            'span:has-text("登录")',
            'text=登录/注册',
        )
        for selector in login_selectors:
            locator = page.locator(selector).first
            with contextlib.suppress(Exception):
                if await locator.count() and await locator.is_visible():
                    return True
        return False

    @staticmethod
    def _is_login_redirect(url: str) -> bool:
        normalized = (url or "").lower()
        return "passport.liepin.com" in normalized or "/login" in normalized

    def _build_search_url(self, base_url: str, search_term: str) -> str:
        parsed = urlparse(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["key"] = (search_term or "").strip()
        query["currentPage"] = "0"
        query["pageSize"] = query.get("pageSize") or "40"
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    async def prepare_search_results_page(
        self,
        list_page,
        target_entry_url: str,
        search_term: str,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        *,
        use_manual_entry_url: bool = False,
    ):
        del use_manual_entry_url

        search_keyword = (search_term or "").strip()
        await list_page.goto(target_entry_url, wait_until="domcontentloaded")
        if await self._sleep_or_stop(1.0, should_stop):
            return list_page
        if not search_keyword:
            if status_callback:
                status_callback("未提供搜索词，使用猎聘默认搜索页继续执行")
            return list_page
        if status_callback:
            status_callback(f"准备在猎聘页面内搜索：{search_keyword}")
        try:
            await self.execute_page_search(list_page, search_keyword, should_stop)
        except Exception:
            logger.warning("Liepin in-page search failed, falling back to query URL.", exc_info=True)
            await list_page.goto(self._build_search_url(target_entry_url, search_keyword), wait_until="domcontentloaded")
            await self._sleep_or_stop(1.2, should_stop)
        return list_page

    async def execute_page_search(self, list_page, search_term: str, should_stop: Callable[[], bool]) -> None:
        keyword = (search_term or "").strip()
        if not keyword:
            return
        search_input = list_page.locator(self.selectors.search_input).first
        await self._wait_for_visible(search_input, should_stop, timeout_ms=12000)
        previous_url = self._safe_page_url(list_page)
        previous_signature = await self._read_page_signature(list_page)

        await search_input.click(timeout=3000)
        with contextlib.suppress(Exception):
            await search_input.press("Control+A")
        await search_input.fill("")
        await search_input.fill(keyword)
        await search_input.press("Enter")

        updated = await self._wait_for_search_results(
            list_page,
            keyword,
            should_stop,
            previous_url=previous_url,
            previous_signature=previous_signature,
        )
        if not updated:
            raise RuntimeError("猎聘搜索后列表未刷新，无法确认搜索已生效")

    async def _wait_for_search_results(
        self,
        list_page,
        search_term: str,
        should_stop: Callable[[], bool],
        *,
        previous_url: str,
        previous_signature: str,
        timeout_ms: int = 12000,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        expected_keyword = (search_term or "").strip()
        while time.time() < deadline:
            if should_stop():
                return False
            current_url = self._safe_page_url(list_page)
            current_signature = await self._read_page_signature(list_page)
            current_keyword = await self._read_current_search_keyword(list_page)
            job_cards = list_page.locator(self.selectors.job_card)
            job_count = 0
            with contextlib.suppress(Exception):
                job_count = await job_cards.count()
            url_keyword = self._read_keyword_from_url(current_url)
            keyword_matches = not expected_keyword or current_keyword == expected_keyword or url_keyword == expected_keyword
            content_updated = bool(
                current_url != previous_url
                or current_signature != previous_signature
                or (job_count > 0 and not previous_signature)
            )
            if keyword_matches and content_updated:
                return True
            await self._sleep_or_stop(0.25, should_stop)
        return False

    async def _read_current_search_keyword(self, list_page) -> str:
        search_input = list_page.locator(self.selectors.search_input).first
        with contextlib.suppress(Exception):
            return self._normalize_text(await search_input.input_value())
        return ""

    @staticmethod
    def _read_keyword_from_url(url: str) -> str:
        parsed = urlparse(url or "")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        return (query.get("key") or "").strip()

    async def open_detail_page(self, context, list_page, card_locator):
        with contextlib.suppress(Exception):
            await list_page.bring_to_front()

        detail_link = card_locator.locator(self.selectors.detail_link).first
        if await detail_link.count() == 0:
            raise RuntimeError("猎聘列表卡片未找到详情链接")

        href = (await detail_link.get_attribute("href") or "").strip()
        if not href:
            raise RuntimeError("猎聘详情链接为空，无法打开岗位详情")
        if href.startswith("/"):
            href = f"https://www.liepin.com{href}"

        detail_page = await context.new_page()
        await detail_page.goto(href, wait_until="domcontentloaded")
        return detail_page

    async def _extract_list_card_title(self, card_locator) -> str:
        with contextlib.suppress(Exception):
            title = await card_locator.evaluate(
                """(el) => {
                    const node = el.querySelector('.job-detail-box [title]') || el.querySelector('a[href*="/lptjob/"]');
                    return (node?.textContent || '').replace(/\\s+/g, ' ').trim();
                }"""
            )
            return self._normalize_text(title)
        return ""

    async def _extract_list_card_job_id(self, card_locator) -> str:
        with contextlib.suppress(Exception):
            href = await card_locator.evaluate(
                """(el) => el.querySelector('a[href*="/lptjob/"]')?.getAttribute('href') || ''"""
            )
            return self.extract_job_code(str(href or ""))
        return ""

    async def _read_page_signature(self, list_page, sample_size: int = 3) -> str:
        cards = list_page.locator(self.selectors.job_card)
        with contextlib.suppress(Exception):
            count = min(await cards.count(), sample_size)
            parts: list[str] = []
            for index in range(count):
                card = cards.nth(index)
                job_id = await self._extract_list_card_job_id(card)
                title = await self._extract_list_card_title(card)
                parts.append(f"{job_id}|{title}")
            return " || ".join(parts)
        return ""

    async def extract_list_card_payload(self, card_locator, list_url: str = "") -> dict:
        summary = {
            "platform_job_id": "",
            "title": "",
            "company_name": "",
            "salary_text": "",
            "city": "",
            "experience_text": "",
            "education_text": "",
            "detail_url": "",
            "tags": [],
        }
        with contextlib.suppress(Exception):
            raw_summary = await card_locator.evaluate(
                """(el) => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const detail = el.querySelector('a[href*="/lptjob/"]');
                    const titleNode = el.querySelector('.job-detail-box [title]') || detail;
                    const cityNode = el.querySelector('._40108__9nJ .ellipsis-1');
                    const salaryNode = el.querySelector('._40108E8PWS');
                    const metaNodes = Array.from(el.querySelectorAll('._40108KeJJy ._40108hJbMl'));
                    const companyNode = el.querySelector('._40108K6Y1c');
                    const companyMetaNodes = Array.from(el.querySelectorAll('._40108hFeAm span'));
                    const rawExt = el.getAttribute('data-tlg-ext') || '';
                    let ext = {};
                    try {
                        ext = rawExt ? JSON.parse(decodeURIComponent(rawExt)) : {};
                    } catch (err) {
                        ext = {};
                    }
                    return {
                        platform_job_id: normalize(String(ext.jobId || '')),
                        title: normalize(titleNode?.textContent || ''),
                        company_name: normalize(companyNode?.textContent || ''),
                        salary_text: normalize(salaryNode?.textContent || ''),
                        city: normalize(cityNode?.textContent || ''),
                        experience_text: normalize(metaNodes[0]?.textContent || ''),
                        education_text: normalize(metaNodes[1]?.textContent || ''),
                        detail_url: detail?.href || '',
                        tags: companyMetaNodes.map((node) => normalize(node.textContent)).filter(Boolean),
                    };
                }"""
            )
            if isinstance(raw_summary, dict):
                summary |= raw_summary

        title = self._normalize_text(str(summary.get("title") or "")) or "未识别职位"
        company_name = self._normalize_text(str(summary.get("company_name") or "")) or "未识别公司"
        salary_text = self._normalize_text(str(summary.get("salary_text") or "")) or "未标注"
        city = self._normalize_text(str(summary.get("city") or "")) or "未标注"
        experience_text = self._normalize_text(str(summary.get("experience_text") or "")) or "不限"
        education_text = self._normalize_text(str(summary.get("education_text") or "")) or "未标注"
        platform_job_id = self._normalize_text(str(summary.get("platform_job_id") or "")) or await self._extract_list_card_job_id(card_locator)
        detail_url = self._normalize_text(str(summary.get("detail_url") or "")) or list_url
        tags = [
            self._normalize_text(str(item))
            for item in summary.get("tags", [])
            if self._normalize_text(str(item))
        ]
        salary_min, salary_max = self.normalize_salary(salary_text)

        return {
            "platform_job_id": platform_job_id,
            "title": title,
            "company_name": company_name,
            "salary_text": salary_text,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "description": "",
            "detail_url": detail_url,
            "tags": tags,
            "city": city,
            "experience_text": experience_text,
            "education_text": education_text,
            "recruiter_name": "",
            "recruiter_status": "",
        }

    async def ensure_list_page(self, context, current_page, target_entry_url: str):
        if current_page is not None and not current_page.is_closed() and await self._is_probable_list_page(current_page, target_entry_url):
            return current_page
        list_pages = await self._collect_list_pages(context, target_entry_url)
        if list_pages:
            return list_pages[0]
        raise RuntimeError("猎聘岗位列表页已被关闭，无法继续执行")

    async def _collect_list_pages(self, context, target_entry_url: str) -> list:
        candidates = []
        for page in list(context.pages):
            if page.is_closed():
                continue
            if await self._is_probable_list_page(page, target_entry_url):
                candidates.append(page)
        return candidates

    async def _is_probable_list_page(self, page, target_entry_url: str) -> bool:
        page_url = self._safe_page_url(page)
        fallback_prefixes = (
            target_entry_url,
            settings.liepin_entry_url,
            "https://www.liepin.com/zhaopin/",
        )
        if any(page_url.startswith(prefix) for prefix in fallback_prefixes if prefix):
            return True
        with contextlib.suppress(Exception):
            if await page.locator(self.selectors.job_card).count():
                return True
        return False

    async def extract_job_payload(self, detail_page, detail_url: str) -> dict:
        description = await self._extract_description(detail_page)
        recruiter_name, recruiter_status = await self._extract_recruiter_info(detail_page)
        company_name, extra_tags = await self._extract_company_info(detail_page)
        platform_job_id = self.extract_job_code(detail_url)
        return {
            "platform_job_id": platform_job_id,
            "description": description,
            "detail_url": detail_url,
            "tags": extra_tags,
            "company_name": company_name,
            "recruiter_name": recruiter_name,
            "recruiter_status": recruiter_status,
        }

    async def _extract_recruiter_info(self, detail_page) -> tuple[str, str]:
        container = detail_page.locator(self.selectors.recruiter_container).first
        with contextlib.suppress(Exception):
            info = await container.evaluate(
                """(el) => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const name = normalize(el.querySelector('.name-box .name')?.textContent || '');
                    const online = normalize(el.querySelector('.name-box .online')?.textContent || '');
                    return { name, online };
                }"""
            )
            if isinstance(info, dict):
                return self._normalize_text(info.get("name")), self._normalize_text(info.get("online"))
        return "", ""

    async def _extract_company_info(self, detail_page) -> tuple[str, list[str]]:
        locator = detail_page.locator(self.selectors.company_info).first
        with contextlib.suppress(Exception):
            info = await locator.evaluate(
                """(el) => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const companyName =
                      normalize(el.querySelector('.company-name')?.textContent || '') ||
                      normalize(el.querySelector('img.logo')?.getAttribute('alt') || '') ||
                      normalize(el.querySelector('a[href*="/company/"]')?.textContent || '');
                    const texts = Array.from(el.querySelectorAll('*'))
                      .map((node) => normalize(node.textContent))
                      .filter(Boolean);
                    const tags = [];
                    for (const item of texts) {
                      if (item.startsWith('企业行业：')) tags.push(item.replace('企业行业：', '').trim());
                      if (item.startsWith('人数规模：')) tags.push(item.replace('人数规模：', '').trim());
                    }
                    return { companyName, tags };
                }"""
            )
            if isinstance(info, dict):
                company_name = self._normalize_text(info.get("companyName"))
                tags = [
                    self._normalize_text(str(item))
                    for item in info.get("tags", [])
                    if self._normalize_text(str(item))
                ]
                return company_name, tags
        return "", []

    async def _extract_description(self, detail_page) -> str:
        locator = detail_page.locator(self.selectors.description).first
        with contextlib.suppress(Exception):
            text = await locator.evaluate(
                """(el) => {
                    if (!(el instanceof HTMLElement)) {
                        return el?.textContent || '';
                    }
                    return el.innerText || el.textContent || '';
                }"""
            )
            return self._normalize_multiline_text(text)
        return self._normalize_multiline_text(await self._safe_inner_text(locator))

    async def detect_apply_success(self, detail_page) -> dict:
        current_url = self._safe_page_url(detail_page)
        if self._is_login_redirect(current_url):
            return {"success": False, "login_required": True, "reason": "投简历后跳转到了登录页，请重新登录猎聘账号"}

        for selector in self.selectors.apply_success_candidates:
            locator = detail_page.locator(selector).first
            with contextlib.suppress(Exception):
                if await locator.count() and await locator.is_visible():
                    return {"success": True, "reason": f"成功检测：已进入猎聘投递成功页 ({selector})"}

        with contextlib.suppress(Exception):
            body_text = await detail_page.locator("body").inner_text()
            if "投递成功" in body_text:
                return {"success": True, "reason": "成功检测：页面已显示投递成功"}

        return {"success": False, "reason": "未检测到猎聘投递成功页"}

    async def detect_delivery_success(self, detail_page) -> dict:
        current_url = self._safe_page_url(detail_page)
        if self._is_login_redirect(current_url):
            return {"success": False, "login_required": True, "reason": "继续聊后跳转到了登录页，请重新登录猎聘账号"}

        for selector in self.selectors.chat_root_candidates:
            locator = detail_page.locator(selector).first
            with contextlib.suppress(Exception):
                if await locator.count() and await locator.is_visible():
                    return {"success": True, "reason": f"成功检测：已打开猎聘沟通窗口 ({selector})", "chat_page": detail_page}

        close_buttons = detail_page.locator(
            ",".join(self.selectors.chat_close_candidates)
        )
        with contextlib.suppress(Exception):
            if await close_buttons.count():
                return {"success": True, "reason": "成功检测：已出现猎聘沟通窗口关闭按钮", "chat_page": detail_page}

        return {"success": False, "reason": "未检测到猎聘沟通窗口"}

    async def try_deliver(self, detail_page, should_stop: Callable[[], bool]) -> dict:
        pre_detect = await self.detect_apply_success(detail_page)
        if pre_detect.get("success", False):
            return {
                "success": True,
                "reason": str(pre_detect.get("reason", "已进入投递成功页")),
                "chat_sent": False,
                "chat_message": "",
                "send_status": "skipped",
            }
        if pre_detect.get("login_required", False):
            return {
                "success": False,
                "reason": pre_detect["reason"],
                "chat_sent": False,
                "chat_message": "",
                "send_status": "skipped",
            }

        button = await self._find_first_visible(
            detail_page,
            self.selectors.apply_button_candidates,
            should_stop,
            timeout_ms=8000,
        )
        if button is None:
            return {
                "success": False,
                "reason": "未找到可点击的投简历按钮",
                "chat_sent": False,
                "chat_message": "",
                "send_status": "skipped",
            }

        with contextlib.suppress(Exception):
            await detail_page.bring_to_front()
        with contextlib.suppress(Exception):
            await button.scroll_into_view_if_needed()
        with contextlib.suppress(Exception):
            await button.hover()

        for click_mode in ("normal", "script"):
            try:
                if click_mode == "normal":
                    await button.click(timeout=5000, no_wait_after=True)
                else:
                    await button.evaluate("(el) => el.click()")
            except Exception:
                logger.warning("Liepin %s apply click failed.", click_mode, exc_info=True)
                continue

            modal_result = await self._wait_for_apply_modal_or_success(detail_page, should_stop)
            if modal_result.get("login_required", False):
                return {
                    "success": False,
                    "reason": f"点击方式：{click_mode} | {modal_result['reason']}",
                    "chat_sent": False,
                    "chat_message": "",
                    "send_status": "skipped",
                }

            if modal_result.get("success", False):
                return {
                    "success": True,
                    "reason": f"点击方式：{click_mode} | {modal_result.get('reason', '已进入投递成功页')}",
                    "chat_sent": False,
                    "chat_message": "",
                    "send_status": "skipped",
                }

            if not modal_result.get("modal_visible", False):
                continue

            confirm_button = await self._find_first_visible(
                detail_page,
                self.selectors.apply_confirm_button_candidates,
                should_stop,
                timeout_ms=4000,
            )
            if confirm_button is None:
                return {
                    "success": False,
                    "reason": f"点击方式：{click_mode} | 已打开附件简历弹窗，但未找到“立即投递”按钮",
                    "chat_sent": False,
                    "chat_message": "",
                    "send_status": "skipped",
                }

            try:
                await confirm_button.click(timeout=5000)
            except Exception:
                logger.warning("Liepin apply confirm click failed.", exc_info=True)
                continue

            delivery_result = await self._wait_for_apply_success(detail_page, should_stop)
            if delivery_result.get("login_required", False):
                return {
                    "success": False,
                    "reason": f"点击方式：{click_mode} | {delivery_result['reason']}",
                    "chat_sent": False,
                    "chat_message": "",
                    "send_status": "skipped",
                }
            if delivery_result.get("success", False):
                return {
                    "success": True,
                    "reason": f"点击方式：{click_mode} | {delivery_result.get('reason', '已进入投递成功页')}",
                    "chat_sent": False,
                    "chat_message": "",
                    "send_status": "skipped",
                }

        return {
            "success": False,
            "reason": "投简历后未检测到附件简历弹窗或投递成功页",
            "chat_sent": False,
            "chat_message": "",
            "send_status": "skipped",
        }

    async def _wait_for_apply_modal_or_success(
        self,
        detail_page,
        should_stop: Callable[[], bool],
        timeout_ms: int = 8000,
    ) -> dict:
        deadline = time.time() + timeout_ms / 1000
        last_reason = "未检测到附件简历弹窗"
        while time.time() < deadline:
            if should_stop():
                return {"success": False, "reason": "收到停止指令，中断投递结果等待"}

            with contextlib.suppress(Exception):
                await detail_page.wait_for_load_state("domcontentloaded", timeout=500)

            success_result = await self.detect_apply_success(detail_page)
            if success_result.get("success", False) or success_result.get("login_required", False):
                return success_result

            modal = detail_page.locator(self.selectors.apply_resume_modal).first
            with contextlib.suppress(Exception):
                if await modal.count() and await modal.is_visible():
                    return {
                        "success": False,
                        "modal_visible": True,
                        "reason": "成功检测：已打开附件简历弹窗",
                    }

            last_reason = success_result.get("reason", last_reason)
            await self._sleep_or_stop(0.25, should_stop)
        return {"success": False, "reason": last_reason}

    async def _wait_for_apply_success(
        self,
        detail_page,
        should_stop: Callable[[], bool],
        timeout_ms: int = 10000,
    ) -> dict:
        deadline = time.time() + timeout_ms / 1000
        last_reason = "未检测到猎聘投递成功页"
        while time.time() < deadline:
            if should_stop():
                return {"success": False, "reason": "收到停止指令，中断投递成功等待"}

            with contextlib.suppress(Exception):
                await detail_page.wait_for_load_state("domcontentloaded", timeout=600)

            result = await self.detect_apply_success(detail_page)
            if result.get("success", False) or result.get("login_required", False):
                return result

            last_reason = result.get("reason", last_reason)
            await self._sleep_or_stop(0.3, should_stop)
        return {"success": False, "reason": last_reason}

    async def wait_for_delivery_signal(
        self,
        detail_page,
        context,
        baseline_page_ids: set[int],
        should_stop: Callable[[], bool],
        timeout_ms: int = 10000,
    ) -> dict:
        deadline = time.time() + timeout_ms / 1000
        last_reason = "未检测到猎聘沟通窗口"
        while time.time() < deadline:
            if should_stop():
                return {"success": False, "reason": "收到停止指令，中断继续聊结果等待"}
            with contextlib.suppress(Exception):
                await detail_page.wait_for_load_state("domcontentloaded", timeout=500)

            detect_result = await self.detect_delivery_success(detail_page)
            if detect_result.get("success", False) or detect_result.get("login_required", False):
                return detect_result

            extra_page_result = await self._detect_new_chat_pages(context, baseline_page_ids)
            if extra_page_result.get("success", False):
                return extra_page_result

            last_reason = detect_result.get("reason", last_reason)
            await self._sleep_or_stop(0.35, should_stop)
        return {"success": False, "reason": last_reason}

    async def _detect_new_chat_pages(self, context, baseline_page_ids: set[int]) -> dict:
        for page in list(context.pages):
            if page.is_closed() or id(page) in baseline_page_ids:
                continue
            page_url = self._safe_page_url(page).lower()
            if "c.liepin.com/job/record/apply" in page_url or "/im/" in page_url:
                return {"success": True, "reason": f"成功检测：继续聊后打开了新页面 {page_url}", "chat_page": page}
            for selector in self.selectors.chat_root_candidates:
                locator = page.locator(selector).first
                with contextlib.suppress(Exception):
                    if await locator.count() and await locator.is_visible():
                        return {"success": True, "reason": f"成功检测：继续聊后在新标签页打开了沟通窗口 {page_url}", "chat_page": page}
        return {"success": False, "reason": ""}

    async def _run_chat_actions(
        self,
        chat_page,
        should_stop: Callable[[], bool],
        *,
        detail_page=None,
    ) -> dict:
        image_result = await self._send_resume_image(chat_page, should_stop)
        text_result = await self._send_text_message(
            chat_page,
            should_stop,
            detail_page=detail_page or chat_page,
        )

        requested = bool(image_result.get("attempted")) or bool(text_result.get("attempted"))
        failures = [
            str(item.get("reason", "")).strip()
            for item in (image_result, text_result)
            if bool(item.get("attempted")) and not bool(item.get("sent")) and str(item.get("reason", "")).strip()
        ]
        successes = [
            str(item.get("summary", "")).strip()
            for item in (image_result, text_result)
            if bool(item.get("sent")) and str(item.get("summary", "")).strip()
        ]

        sent_messages = [
            str(item.get("chat_message", "")).strip()
            for item in (image_result, text_result)
            if bool(item.get("sent")) and str(item.get("chat_message", "")).strip()
        ]
        fallback_messages = [
            str(item.get("chat_message", "")).strip()
            for item in (image_result, text_result)
            if str(item.get("chat_message", "")).strip()
        ]

        chat_message = " | ".join(sent_messages or fallback_messages)
        chat_sent = bool(image_result.get("sent")) or bool(text_result.get("sent"))
        send_status = "skipped"
        if requested:
            send_status = "sent" if chat_sent and not failures else "failed"

        summary_parts = successes + failures
        return {
            "chat_sent": chat_sent,
            "chat_message": chat_message,
            "send_status": send_status,
            "status_summary": "；".join(summary_parts),
        }

    async def _send_resume_image(self, chat_page, should_stop: Callable[[], bool]) -> dict:
        if not self._enable_image_resume:
            return {"attempted": False, "sent": False, "reason": "", "summary": "", "chat_message": ""}

        raw_path = (self._image_resume_path or "").strip()
        if not raw_path:
            return {
                "attempted": True,
                "sent": False,
                "reason": "未配置简历图片",
                "summary": "",
                "chat_message": "",
            }

        image_path = Path(raw_path)
        if not image_path.exists():
            logger.warning("Liepin image resume path not found: %s", raw_path)
            return {
                "attempted": True,
                "sent": False,
                "reason": f"简历图片不存在: {raw_path}",
                "summary": "",
                "chat_message": "",
            }

        baseline_count = await self._count_image_messages(chat_page)
        file_input = await self._find_first_attached(
            chat_page,
            self.selectors.chat_image_input_candidates,
            should_stop,
            timeout_ms=4000,
        )
        if file_input is None:
            return {
                "attempted": True,
                "sent": False,
                "reason": "未找到猎聘图片上传控件",
                "summary": "",
                "chat_message": "",
            }

        try:
            await file_input.set_input_files(str(image_path))
        except Exception:
            logger.warning("Liepin image upload failed: %s", raw_path, exc_info=True)
            return {
                "attempted": True,
                "sent": False,
                "reason": f"简历图片上传失败: {image_path.name}",
                "summary": "",
                "chat_message": "",
            }

        confirmed = await self._confirm_image_send_if_needed(chat_page, should_stop)
        if not confirmed:
            return {
                "attempted": True,
                "sent": False,
                "reason": "简历图片确认发送失败",
                "summary": "",
                "chat_message": "",
            }

        sent = await self._wait_for_new_image_message(chat_page, baseline_count, should_stop)
        if not sent:
            return {
                "attempted": True,
                "sent": False,
                "reason": f"未检测到简历图片发送成功: {image_path.name}",
                "summary": "",
                "chat_message": "",
            }

        return {
            "attempted": True,
            "sent": True,
            "reason": "",
            "summary": "已发送简历图片",
            "chat_message": f"已发送简历图片：{image_path.name}",
        }

    async def _send_text_message(
        self,
        chat_page,
        should_stop: Callable[[], bool],
        *,
        detail_page,
    ) -> dict:
        if not self._auto_send_chat:
            return {"attempted": False, "sent": False, "reason": "", "summary": "", "chat_message": ""}

        job_title = self.extract_job_code(self._safe_page_url(detail_page))
        with contextlib.suppress(Exception):
            page_title = await detail_page.title()
            if page_title:
                job_title = page_title.split("_")[0].split("-")[0].strip() or job_title
        company_name, _ = await self._extract_company_info(detail_page)
        if not company_name and chat_page is not detail_page:
            company_name, _ = await self._extract_company_info(chat_page)
        message = self._build_chat_message(job_title, company_name)
        if not message:
            return {"attempted": False, "sent": False, "reason": "", "summary": "", "chat_message": ""}

        textarea = await self._find_first_visible(
            chat_page,
            self.selectors.chat_textarea_candidates,
            should_stop,
            timeout_ms=5000,
        )
        if textarea is None:
            return {
                "attempted": True,
                "sent": False,
                "reason": "未找到猎聘聊天输入框",
                "summary": "",
                "chat_message": message,
            }

        try:
            await textarea.click(timeout=3000)
            with contextlib.suppress(Exception):
                await textarea.press("Control+A")
            await textarea.fill("")
            await textarea.fill(message)
        except Exception:
            logger.warning("Liepin chat textarea fill failed.", exc_info=True)
            return {
                "attempted": True,
                "sent": False,
                "reason": "猎聘追加消息填写失败",
                "summary": "",
                "chat_message": message,
            }

        send_button = await self._find_first_visible(
            chat_page,
            self.selectors.chat_send_button_candidates,
            should_stop,
            timeout_ms=3000,
        )
        try:
            if send_button is not None:
                await send_button.click(timeout=3000)
            else:
                await textarea.press("Enter")
        except Exception:
            logger.warning("Liepin chat send click failed.", exc_info=True)
            return {
                "attempted": True,
                "sent": False,
                "reason": "猎聘追加消息发送失败",
                "summary": "",
                "chat_message": message,
            }

        await self._wait_for_message_echo(chat_page, message, should_stop)
        return {
            "attempted": True,
            "sent": True,
            "reason": "",
            "summary": "已发送追加消息",
            "chat_message": message,
        }

    def _build_chat_message(self, job_title: str, company_name: str) -> str:
        template = self._chat_message_template.strip() if self._use_chat_template else ""
        if not template:
            template = "您好，我刚刚关注了贵司的 {job_title} 岗位，期待进一步沟通，谢谢。"
        return (
            template.replace("{job_title}", job_title or "该岗位")
            .replace("{company_name}", company_name or "贵司")
            .strip()
        )

    async def _confirm_image_send_if_needed(
        self,
        chat_page,
        should_stop: Callable[[], bool],
        timeout_ms: int = 4000,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        modal = chat_page.locator(self.selectors.chat_image_confirm_modal).first
        while time.time() < deadline:
            if should_stop():
                return False
            with contextlib.suppress(Exception):
                if await modal.count() and await modal.is_visible():
                    confirm_button = await self._find_first_visible(
                        chat_page,
                        self.selectors.chat_image_confirm_button_candidates,
                        should_stop,
                        timeout_ms=2000,
                    )
                    if confirm_button is None:
                        return False
                    await confirm_button.click(timeout=2000)
                    return await self._wait_for_locator_hidden(modal, should_stop, timeout_ms=4000)
            await self._sleep_or_stop(0.2, should_stop)
        return True

    async def _wait_for_new_image_message(
        self,
        chat_page,
        baseline_count: int,
        should_stop: Callable[[], bool],
        timeout_ms: int = 8000,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if should_stop():
                return False
            current_count = await self._count_image_messages(chat_page)
            if current_count > baseline_count:
                return True
            await self._sleep_or_stop(0.25, should_stop)
        return False

    async def _count_image_messages(self, chat_page) -> int:
        count = 0
        for selector in self.selectors.chat_image_message_candidates:
            locator = chat_page.locator(selector)
            with contextlib.suppress(Exception):
                count = max(count, await locator.count())
        return count

    async def _wait_for_locator_hidden(
        self,
        locator,
        should_stop: Callable[[], bool],
        timeout_ms: int = 4000,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if should_stop():
                return False
            with contextlib.suppress(Exception):
                if await locator.count() == 0 or not await locator.is_visible():
                    return True
            await self._sleep_or_stop(0.2, should_stop)
        return False

    async def _wait_for_message_echo(
        self,
        detail_page,
        message: str,
        should_stop: Callable[[], bool],
        timeout_ms: int = 4000,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if should_stop():
                return False
            with contextlib.suppress(Exception):
                page_text = await detail_page.locator("body").inner_text()
                if message and message in page_text:
                    return True
            await self._sleep_or_stop(0.25, should_stop)
        return False

    async def _close_chat_overlays(self, detail_page, should_stop: Callable[[], bool]) -> None:
        for _ in range(3):
            clicked = False
            for selector in self.selectors.chat_close_candidates:
                locator = detail_page.locator(selector).first
                with contextlib.suppress(Exception):
                    if await locator.count() and await locator.is_visible():
                        await locator.click(timeout=1500)
                        clicked = True
                        await self._sleep_or_stop(0.3, should_stop)
                        break
            if not clicked:
                return

    async def _find_first_visible(
        self,
        page,
        selectors: tuple[str, ...],
        should_stop: Callable[[], bool],
        timeout_ms: int = 4000,
    ):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if should_stop():
                return None
            for selector in selectors:
                locator = page.locator(selector).first
                with contextlib.suppress(Exception):
                    if await locator.count() and await locator.is_visible():
                        return locator
            await self._sleep_or_stop(0.2, should_stop)
        return None

    async def _find_first_attached(
        self,
        page,
        selectors: tuple[str, ...],
        should_stop: Callable[[], bool],
        timeout_ms: int = 4000,
    ):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if should_stop():
                return None
            for selector in selectors:
                locator = page.locator(selector).first
                with contextlib.suppress(Exception):
                    if await locator.count():
                        return locator
            await self._sleep_or_stop(0.2, should_stop)
        return None
