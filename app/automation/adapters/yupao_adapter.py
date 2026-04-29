import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.automation.adapters.liepin_adapter import LiepinAdapter
from app.automation.browser_factory import (
    LAODENG_INIT_SCRIPT,
    STEALTH_INIT_SCRIPT,
    build_browser_launch_options,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class YupaoSelectors:
    search_input: str = 'input[placeholder="搜索职位、公司"]'
    job_card: str = 'a[href*="/zhaogong/"]:has(span):not([href*="/zhaogong/a"])'
    detail_link: str = 'a[href*="/zhaogong/"]:has(span):not([href*="/zhaogong/a"])'
    active_page: str = ".yp-pagelist-infinite"
    next_page: str = ".yp-pagelist-infinite"
    description: str = "h1"
    recruiter_container: str = "h1"
    company_info: str = 'a[href*="/qiye/"]'
    apply_button_candidates: tuple[str, ...] = (
        "text=发送简历",
        "text=投递简历",
        "text=发简历",
    )
    apply_resume_modal: str = '.dialog, .modal, .popup, .van-dialog, [role="dialog"]'
    apply_confirm_button_candidates: tuple[str, ...] = (
        "text=发送",
        "text=发简历",
        "text=确定",
        "text=立即沟通",
        "text=立即联系",
        "text=投递",
    )
    apply_success_candidates: tuple[str, ...] = (
        "text=投递成功",
        "text=简历发送成功",
        "text=投递已发送",
    )
    continue_chat_button_candidates: tuple[str, ...] = (
        "text=继续聊",
        "text=继续沟通",
    )
    chat_button_candidates: tuple[str, ...] = (
        "text=免费聊",
        "text=继续聊",
        "text=继续沟通",
        "text=聊一聊",
    )
    chat_root_candidates: tuple[str, ...] = (
        'div.fb-editor[data-fish-editor="true"]',
        "button.btn-send",
        '[contenteditable="true"]',
        "text=按Enter键发送",
    )
    chat_textarea_candidates: tuple[str, ...] = (
        'div.fb-editor[data-fish-editor="true"]',
        '[contenteditable="true"]',
        "textarea",
        "input[type='text']",
    )
    chat_send_button_candidates: tuple[str, ...] = (
        "button.btn-send",
        "text=发送",
    )
    chat_image_input_candidates: tuple[str, ...] = ('input[type="file"]',)
    chat_image_confirm_modal: str = '.dialog, .modal, .popup, [role="dialog"]'
    chat_image_confirm_button_candidates: tuple[str, ...] = (
        "text=确定",
        "text=发送",
    )
    chat_image_message_candidates: tuple[str, ...] = ("img",)
    chat_close_candidates: tuple[str, ...] = ()


class YupaoAdapter(LiepinAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.selectors = YupaoSelectors()

    @staticmethod
    def _is_salary_candidate(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return False
        if normalized == "面议":
            return True
        return bool(
            re.search(r"(?:\d|[一二三四五六七八九十]).*(?:K|k|千|万|元/月|元/天|万元/月|千/月)", normalized)
        ) and len(normalized) <= 24

    def _normalize_text_list(self, values) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value is None:
                continue
            text = self._normalize_text(str(value))
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized_values.append(text)
        return normalized_values

    def _extract_salary_text(self, values: list[str], *fallback_texts: str) -> str:
        for text in values:
            if self._is_salary_candidate(text):
                return text
        for raw_text in fallback_texts:
            text = self._normalize_text(raw_text)
            if not text:
                continue
            match = re.search(
                r"(面议|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?\s*(?:K|k|千|万|元/月|元/天|万元/月|千/月))",
                text,
            )
            if match:
                return self._normalize_text(match.group(1))
        return ""

    def _extract_experience_text(self, values: list[str], *fallback_texts: str) -> str:
        exact_pattern = re.compile(
            r"^(经验不限|无经验|应届生|在校生|1年以内|1年以下|\d+(?:-\d+)?年(?:以上|以内|以下|及以上)?)$"
        )
        search_pattern = re.compile(
            r"(经验不限|无经验|应届生|在校生|1年以内|1年以下|\d+(?:-\d+)?年(?:以上|以内|以下|及以上)?)"
        )
        for text in values:
            if len(text) <= 18 and exact_pattern.fullmatch(text):
                return text
        for text in [*values, *[self._normalize_text(item) for item in fallback_texts]]:
            if not text or len(text) > 120:
                continue
            match = search_pattern.search(text)
            if match:
                return self._normalize_text(match.group(1))
        return ""

    def _extract_education_text(self, values: list[str], *fallback_texts: str) -> str:
        exact_pattern = re.compile(
            r"^(学历不限|初中(?:及以下)?|中专|中技|高中|大专|本科(?:及以上)?|硕士(?:及以上)?|博士(?:及以上)?)$"
        )
        search_pattern = re.compile(
            r"(学历不限|初中(?:及以下)?|中专|中技|高中|大专|本科|硕士|博士)"
        )
        for text in values:
            if len(text) <= 16 and exact_pattern.fullmatch(text):
                return text
        for text in [*values, *[self._normalize_text(item) for item in fallback_texts]]:
            if not text or len(text) > 120:
                continue
            match = search_pattern.search(text)
            if match:
                return self._normalize_text(match.group(1))
        return ""

    def _extract_city_text(self, values: list[str], exclude: set[str] | None = None) -> str:
        exclude = exclude or set()
        city_pattern = re.compile(
            r"(市|区|县|镇|广州|深圳|北京|上海|杭州|苏州|佛山|东莞|珠海|成都|重庆|天津|武汉|南京|郑州|长沙|青岛|宁波|厦门)"
        )
        for text in values:
            if text in exclude or len(text) > 24:
                continue
            if city_pattern.search(text):
                return text
        return ""

    def _pick_company_name(self, candidates: list[str], fallback_values: list[str] | None = None) -> str:
        placeholders = {"公司", "企业", "查看企业信息", "工商信息"}
        for text in candidates:
            if text not in placeholders:
                return text
        for text in fallback_values or []:
            if text in placeholders or len(text) > 40:
                continue
            if re.search(r"(公司|集团|科技|信息|有限|企业|工作室|中心|工厂|厂|店)$", text):
                return text
        return ""

    def _filter_short_tags(self, values: list[str], exclude: set[str]) -> list[str]:
        blocked_pattern = re.compile(
            r"发送简历|投递简历|发简历|免费聊|继续聊|继续沟通|投递成功|职位详情|工作地址|查看企业信息|"
            r"查看更多信息|热门职位|相同职位推荐|附近招聘|职位大全|点击查看地图|法定代表人|成立日期|"
            r"微信扫码分享|投诉|按Enter键发送|换电话|换微信|发消息|招聘人数|薪资报酬|招聘有效期"
        )
        tags: list[str] = []
        seen: set[str] = set()
        for text in values:
            if text in exclude or len(text) > 24:
                continue
            if blocked_pattern.search(text):
                continue
            if self._is_salary_candidate(text):
                continue
            if self._extract_experience_text([text]) or self._extract_education_text([text]):
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            tags.append(text)
        return tags[:12]

    async def _inspect_delivery_state(self, detail_page) -> dict:
        current_url = self._safe_page_url(detail_page).lower()
        if self._is_login_redirect(current_url):
            return {
                "login_required": True,
                "reason": "鱼泡流程跳转到了登录页，请重新登录账号",
                "button_texts": [],
                "has_apply_button": False,
                "has_continue_chat": False,
                "has_success_toast": False,
            }

        state = await detail_page.evaluate(
            """() => {
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (el) => {
                    if (!(el instanceof HTMLElement)) return false;
                    const style = window.getComputedStyle(el);
                    if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const resolveDetailRoot = () => {
                    const heading = document.querySelector('h1');
                    let cursor = heading;
                    while (cursor && cursor.parentElement) {
                        const text = normalize(cursor.innerText || cursor.textContent || '');
                        if (text && /发送简历|投递简历|发简历|免费聊|继续聊|继续沟通/.test(text) && text.length <= 2500) {
                            return cursor;
                        }
                        cursor = cursor.parentElement;
                    }
                    return heading || document.querySelector('main') || document.body;
                };
                const pattern = /发送简历|投递简历|发简历|免费聊|继续聊|继续沟通|投递成功|简历发送成功|投递已发送/;
                const pushUnique = (items, seen, value) => {
                    const text = normalize(value);
                    if (!text || text.length > 24 || seen.has(text)) return;
                    seen.add(text);
                    items.push(text);
                };
                const buttonTexts = [];
                const seen = new Set();
                const detailRoot = resolveDetailRoot();
                const nodes = Array.from(detailRoot.querySelectorAll('button, a, [role="button"], div, span'));
                for (const node of nodes) {
                    const text = normalize(node.innerText || node.textContent || '');
                    if (!pattern.test(text) || !isVisible(node)) continue;
                    pushUnique(buttonTexts, seen, text);
                }
                const bodyText = normalize(detailRoot.innerText || detailRoot.textContent || '');
                return { buttonTexts, bodyText };
            }"""
        )
        button_texts = self._normalize_text_list(state.get("buttonTexts", []))
        body_text = self._normalize_text(str(state.get("bodyText") or ""))
        has_apply_button = any(
            text in {"发送简历", "投递简历", "发简历"} for text in button_texts
        )
        has_continue_chat = any(text in {"继续聊", "继续沟通"} for text in button_texts)
        has_success_toast = any(
            marker in body_text for marker in ("投递成功", "简历发送成功", "投递已发送")
        )
        return {
            "login_required": False,
            "reason": "",
            "button_texts": button_texts,
            "has_apply_button": has_apply_button,
            "has_continue_chat": has_continue_chat,
            "has_success_toast": has_success_toast,
            "already_delivered": has_continue_chat and not has_apply_button,
        }

    @staticmethod
    def _format_delivery_state(state: dict | None) -> str:
        if not state:
            return "visible_buttons=<unknown>"
        button_texts = state.get("button_texts") or []
        button_summary = ", ".join(button_texts) if button_texts else "<none>"
        flags: list[str] = [f"visible_buttons={button_summary}"]
        if state.get("has_success_toast"):
            flags.append("toast=投递成功")
        return " | ".join(flags)

    @staticmethod
    def extract_job_code(detail_url: str) -> str:
        if not detail_url:
            return ""
        match = re.search(r"/zhaogong/[^/?#]*?(\d{5,})", detail_url)
        if match:
            return match.group(1)
        match = re.search(r"(\d{5,})", detail_url)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def normalize_salary(raw_salary: str) -> tuple[float | None, float | None]:
        return LiepinAdapter.normalize_salary(raw_salary)

    @staticmethod
    def _is_login_redirect(url: str) -> bool:
        normalized = (url or "").lower()
        return "login" in normalized or "passport" in normalized

    async def run_search_flow(
        self,
        search_terms: list[str],
        profile_dir: str,
        per_job_callback: Callable[[dict], None],
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
        job_precheck_callback: Callable[[dict], dict[str, str | bool]] | None = None,
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
        target_entry_url = (entry_url or settings.yupao_entry_url).strip() or settings.yupao_entry_url
        chat_url = settings.yupao_chat_url
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
                    raise RuntimeError("鱼泡外部 Chrome 已启动，但未能连接到调试端口。")
                browser = await playwright.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            self._bind_runtime(asyncio.get_running_loop(), context, browser, chrome_pid)
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.add_init_script(LAODENG_INIT_SCRIPT)
            list_page, chat_page = await self._ensure_pages(context, target_entry_url, chat_url)
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
                if await self.is_login_required(list_page, chat_page):
                    if status_callback:
                        status_callback("鱼泡当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                    await self.wait_for_login(
                        list_page,
                        chat_page,
                        should_stop,
                        status_callback=status_callback,
                        confirm_login_callback=confirm_login_callback,
                    )
                    if should_stop():
                        return

                for search_term in effective_search_terms:
                    if should_stop():
                        break

                    processed_job_keys: set[str] = set()
                    search_label = (search_term or "").strip() or "默认列表页"
                    preview_ref["page"] = list_page
                    list_page = await self.prepare_search_results_page(
                        list_page,
                        target_entry_url,
                        search_term,
                        should_stop,
                        status_callback,
                        use_manual_entry_url=entry_url is not None,
                    )
                    if await self.is_login_required(list_page, chat_page):
                        if status_callback:
                            status_callback("鱼泡当前未登录，请先在浏览器中完成登录，然后回到前端点击确认已登录。")
                        await self.wait_for_login(
                            list_page,
                            chat_page,
                            should_stop,
                            status_callback=status_callback,
                            confirm_login_callback=confirm_login_callback,
                        )
                        if should_stop():
                            return
                        list_page = await self.prepare_search_results_page(
                            list_page,
                            target_entry_url,
                            search_term,
                            should_stop,
                            status_callback,
                            use_manual_entry_url=entry_url is not None,
                        )

                    while not should_stop():
                        list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                        cards = list_page.locator(self.selectors.job_card)
                        count = await cards.count()
                        if count == 0:
                            if status_callback:
                                status_callback(f"{search_label} 当前页未找到鱼泡岗位卡片，切换下一组条件。")
                            break

                        new_job_found = False
                        for index in range(count):
                            if should_stop():
                                break

                            list_page = await self.ensure_list_page(context, list_page, target_entry_url)
                            cards = list_page.locator(self.selectors.job_card)
                            current_count = await cards.count()
                            if index >= current_count:
                                break

                            card_locator = cards.nth(index)
                            list_card_payload = await self.extract_list_card_payload(
                                card_locator,
                                self._safe_page_url(list_page),
                            )
                            job_key = self._build_job_key(list_card_payload, fallback_index=index)
                            if job_key in processed_job_keys:
                                continue

                            processed_job_keys.add(job_key)
                            new_job_found = True
                            if status_callback:
                                status_callback(f"{search_label}：正在打开第 {len(processed_job_keys)} 个鱼泡岗位详情")

                            pages_before_detail = self._snapshot_open_pages(context)
                            detail_page = await self.open_detail_page(context, list_page, card_locator, should_stop)
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
                                job_payload = self._merge_job_payload(list_card_payload, detail_payload)
                                job_payload["search_term"] = search_label
                                job_payload["matched"] = True
                                job_payload["already_delivered"] = False

                                if job_precheck_callback:
                                    precheck = job_precheck_callback(job_payload)
                                    job_payload["matched"] = bool(precheck.get("matched", True))
                                    job_payload["status_reason"] = str(precheck.get("reason", "命中过滤条件"))

                                if not job_payload["matched"]:
                                    job_payload["delivery_success"] = False
                                    job_payload["chat_sent"] = False
                                    job_payload["chat_message"] = ""
                                    job_payload["send_status"] = "skipped"
                                    continue

                                delivery_result = await self.try_deliver(
                                    detail_page,
                                    should_stop,
                                    context=context,
                                    baseline_page_ids=pages_before_detail,
                                    chat_page=chat_page,
                                )
                                job_payload["already_delivered"] = bool(delivery_result.get("already_delivered", False))
                                job_payload["delivery_success"] = bool(delivery_result.get("success", False)) and not job_payload["already_delivered"]
                                job_payload["status_reason"] = str(delivery_result.get("reason", "未完成鱼泡投递"))
                                job_payload["chat_sent"] = bool(delivery_result.get("chat_sent", False))
                                job_payload["chat_message"] = str(delivery_result.get("chat_message", ""))
                                job_payload["send_status"] = str(delivery_result.get("send_status", ""))
                            except (PlaywrightTimeoutError, TimeoutError):
                                logger.info("Yupao detail flow timed out for %s", list_card_payload.get("detail_url", ""))
                                if job_payload is None:
                                    job_payload = dict(list_card_payload)
                                    job_payload["search_term"] = search_label
                                job_payload["delivery_success"] = False
                                job_payload["status_reason"] = "鱼泡职位详情处理超时"
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

                            if delivery_interval_seconds > 0 and await self._sleep_or_stop(
                                float(delivery_interval_seconds),
                                should_stop,
                            ):
                                break

                        if should_stop():
                            break
                        if not new_job_found:
                            if status_callback:
                                status_callback(f"{search_label} 当前已没有新的鱼泡岗位卡片。")
                            break

                        loaded_more = await self.go_to_next_page(
                            list_page,
                            should_stop,
                            status_callback,
                            previous_count=count,
                        )
                        if not loaded_more:
                            break
                        if await self._sleep_or_stop(1.0, should_stop):
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

    async def _ensure_pages(self, context, list_url: str, chat_url: str):
        pages = [page for page in context.pages if not (page.url or "").startswith("chrome://")]
        list_page = pages[0] if pages else await context.new_page()
        chat_page = await context.new_page()

        await list_page.bring_to_front()
        await list_page.goto(list_url, wait_until="domcontentloaded")
        await list_page.wait_for_timeout(1500)

        await chat_page.bring_to_front()
        await chat_page.goto(chat_url, wait_until="domcontentloaded")
        await chat_page.wait_for_timeout(2000)

        logger.info(
            "Yupao pages prepared: total=%s list_url=%s chat_url=%s context_pages=%s",
            len(context.pages),
            list_page.url,
            chat_page.url,
            [page.url for page in context.pages],
        )
        await list_page.bring_to_front()
        return list_page, chat_page

    async def is_login_required(self, *pages) -> bool:
        for page in pages:
            current_url = self._safe_page_url(page).lower()
            if self._is_login_redirect(current_url):
                return True
            for selector in ('a:has-text("登录")', 'button:has-text("登录")', 'text=登录/注册', 'text=立即登录'):
                locator = page.locator(selector).first
                with contextlib.suppress(Exception):
                    if await locator.count() and await locator.is_visible():
                        text = self._normalize_text(await locator.inner_text())
                        if "登录" in text or "注册" in text:
                            return True
        return False

    async def wait_for_login(
        self,
        list_page,
        chat_page,
        should_stop: Callable[[], bool],
        *,
        status_callback: Callable[[str], None] | None = None,
        confirm_login_callback: Callable[[], bool] | None = None,
    ) -> None:
        while not should_stop():
            if confirm_login_callback and confirm_login_callback():
                with contextlib.suppress(Exception):
                    await list_page.reload(wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    await chat_page.reload(wait_until="domcontentloaded")
                if await self.is_login_required(list_page, chat_page):
                    if status_callback:
                        status_callback("仍检测到鱼泡未登录，请完成登录后再次确认。")
                else:
                    if status_callback:
                        status_callback("已确认鱼泡登录完成，开始执行投递与聊天。")
                    return
            if await self._sleep_or_stop(2.0, should_stop):
                return

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
        search_keyword = (search_term or "").strip()
        search_label = search_keyword or "默认列表页"
        open_message = "正在打开手动配置的鱼泡列表页" if use_manual_entry_url else "正在打开鱼泡职位页"
        if status_callback:
            suffix = f"：{search_label}" if search_keyword or not use_manual_entry_url else ""
            status_callback(f"{open_message}{suffix}")

        await list_page.goto(target_entry_url, wait_until="domcontentloaded")
        if await self._sleep_or_stop(1.2, should_stop):
            return list_page
        if not search_keyword:
            if status_callback:
                status_callback("未提供搜索词，使用鱼泡当前列表页继续执行")
            return list_page

        if status_callback:
            status_callback(f"准备在鱼泡页面内搜索：{search_keyword}")
        await self.execute_page_search(list_page, search_keyword, should_stop)
        return list_page

    async def _read_detail_panel_signature(self, page) -> str:
        with contextlib.suppress(Exception):
            signature = await page.evaluate(
                """() => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const heading = document.querySelector('h1');
                    if (!heading) {
                        return '';
                    }
                    let container = heading;
                    while (container && container.parentElement) {
                        const text = normalize(container.innerText || container.textContent || '');
                        if (text && /发送简历|投递简历|发简历|免费聊|继续聊|继续沟通/.test(text) && text.length <= 2500) {
                            break;
                        }
                        container = container.parentElement;
                    }
                    container = container || heading;
                    const title = normalize(heading.textContent || '');
                    const company = normalize(container.querySelector('a[href*="/qiye/"]')?.textContent || '');
                    return `${title}|${company}`;
                }"""
            )
            return self._normalize_text(signature)
        return ""

    async def open_detail_page(self, context, list_page, card_locator, should_stop: Callable[[], bool]):
        with contextlib.suppress(Exception):
            await list_page.bring_to_front()
        expected_job_id = await self._extract_list_card_job_id(card_locator)
        expected_title = await self._extract_list_card_title(card_locator)
        previous_signature = await self._read_detail_panel_signature(list_page)

        for click_mode in ("normal", "script"):
            try:
                if click_mode == "normal":
                    await card_locator.click(timeout=5000, no_wait_after=True)
                else:
                    await card_locator.evaluate("(el) => el.click()")
            except Exception:
                logger.warning("Yupao list card click failed: mode=%s title=%s", click_mode, expected_title, exc_info=True)
                continue

            ready = await self._wait_for_detail_ready(
                list_page,
                should_stop,
                expected_job_id=expected_job_id,
                expected_title=expected_title,
                previous_signature=previous_signature,
            )
            if ready:
                return list_page

        raise RuntimeError("鱼泡列表卡片点击后未进入岗位详情")

    async def _wait_for_detail_ready(
        self,
        page,
        should_stop: Callable[[], bool],
        *,
        expected_job_id: str = "",
        expected_title: str = "",
        previous_signature: str = "",
        timeout_ms: int = 8000,
    ) -> bool:
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if should_stop():
                return False
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=500)
            try:
                state = await page.evaluate(
                    """(jobId, jobTitle, beforeSignature) => {
                        const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                        const resolveDetailRoot = () => {
                            const heading = document.querySelector('h1');
                            let cursor = heading;
                            while (cursor && cursor.parentElement) {
                                const text = normalize(cursor.innerText || cursor.textContent || '');
                                if (text && /发送简历|投递简历|发简历|免费聊|继续聊|继续沟通/.test(text) && text.length <= 2500) {
                                    return cursor;
                                }
                                cursor = cursor.parentElement;
                            }
                            return heading || document.querySelector('main') || document.body;
                        };
                        const detailRoot = resolveDetailRoot();
                        const title = normalize(document.querySelector('h1')?.textContent || '');
                        const panelText = normalize(detailRoot.innerText || detailRoot.textContent || '');
                        const company = normalize(detailRoot.querySelector('a[href*="/qiye/"]')?.textContent || '');
                        const signature = `${title}|${company}`;
                        const detailHrefs = Array.from(detailRoot.querySelectorAll('a[href*="/zhaogong/"]'))
                            .map((node) => node.href || '');
                        const hasAction = /发送简历|继续聊|免费聊|投递简历|发简历/.test(panelText);
                        const jobMatch = jobId ? detailHrefs.some((href) => href.includes(jobId)) : false;
                        const titleMatch = jobTitle ? (
                            title === jobTitle
                            || title.includes(jobTitle)
                            || jobTitle.includes(title)
                        ) : false;
                        const signatureChanged = beforeSignature ? Boolean(signature && signature !== beforeSignature) : false;
                        return { title, company, signature, hasAction, jobMatch, titleMatch, signatureChanged };
                    }""",
                    expected_job_id,
                    expected_title,
                    previous_signature,
                )
            except Exception:
                await self._sleep_or_stop(0.25, should_stop)
                continue

            if bool(state.get("hasAction")) and (
                bool(state.get("titleMatch")) or bool(state.get("jobMatch")) or bool(state.get("signatureChanged"))
            ):
                return True
            await self._sleep_or_stop(0.25, should_stop)
        return False

    async def _extract_list_card_title(self, card_locator) -> str:
        with contextlib.suppress(Exception):
            text = await card_locator.evaluate(
                """(el) => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const root = el.closest('a[href*="/zhaogong/"]') || el;
                    const container = root.parentElement || root;
                    const lines = (container.innerText || root.innerText || '').split('\\n').map(normalize).filter(Boolean);
                    return lines[0] || normalize(root.getAttribute('title')) || normalize(root.textContent);
                }"""
            )
            return self._normalize_text(text)
        return ""

    async def _extract_list_card_job_id(self, card_locator) -> str:
        with contextlib.suppress(Exception):
            href = (await card_locator.get_attribute("href") or "").strip()
            return self.extract_job_code(href)
        return ""

    async def _read_page_signature(self, list_page, sample_size: int = 2) -> str:
        cards = list_page.locator(self.selectors.job_card)
        with contextlib.suppress(Exception):
            total = await cards.count()
            if total == 0:
                return ""
            indexes = list(range(min(total, sample_size)))
            tail_start = max(total - sample_size, 0)
            for index in range(tail_start, total):
                if index not in indexes:
                    indexes.append(index)
            parts: list[str] = []
            for index in indexes:
                card = cards.nth(index)
                job_id = await self._extract_list_card_job_id(card)
                title = await self._extract_list_card_title(card)
                parts.append(f"{index}:{job_id}|{title}")
            return " || ".join(parts)
        return ""

    async def extract_list_card_payload(self, card_locator, list_url: str = "") -> dict:
        payload = await card_locator.evaluate(
            """(el) => {
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const unique = (items) => {
                    const seen = new Set();
                    const values = [];
                    for (const item of items) {
                        const text = normalize(item);
                        if (!text || seen.has(text)) continue;
                        seen.add(text);
                        values.push(text);
                    }
                    return values;
                };
                const root = el.closest('a[href*="/zhaogong/"]') || el;
                const container = root.parentElement || root;
                const lines = unique(
                    (container.innerText || root.innerText || '')
                        .split('\\n')
                        .map(normalize)
                        .filter(Boolean)
                );
                const href = root.getAttribute('href') || '';
                const title = lines[0] || normalize(root.getAttribute('title')) || normalize(root.textContent);
                const companyCandidates = unique(
                    Array.from(container.querySelectorAll('a[href*="/qiye/"], [class*="company"]'))
                        .map((node) => normalize(node.textContent))
                        .filter(Boolean)
                );
                return { href, title, lines, companyCandidates };
            }"""
        )
        detail_url = self._normalize_text(str(payload.get("href") or ""))
        if detail_url.startswith("/"):
            detail_url = f"https://www.yupao.com{detail_url}"
        title = self._normalize_text(str(payload.get("title") or ""))
        lines = self._normalize_text_list(payload.get("lines", []))
        company_candidates = self._normalize_text_list(payload.get("companyCandidates", []))
        salary_text = self._extract_salary_text(lines)
        experience_text = self._extract_experience_text(lines[1:])
        education_text = self._extract_education_text(lines[1:])
        company_name = self._pick_company_name(company_candidates, lines)
        city = self._extract_city_text(
            lines,
            exclude={title, salary_text, company_name, experience_text, education_text},
        )
        tags = self._filter_short_tags(
            lines,
            {title, salary_text, company_name, city, experience_text, education_text},
        )
        salary_min, salary_max = self.normalize_salary(salary_text)
        return {
            "platform_job_id": self.extract_job_code(detail_url),
            "title": title or "未命名岗位",
            "company_name": company_name or "未识别公司",
            "salary_text": salary_text,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "description": "",
            "detail_url": detail_url or list_url,
            "tags": tags,
            "city": city,
            "experience_text": experience_text,
            "education_text": education_text,
            "recruiter_name": "",
            "recruiter_status": "",
        }

    async def ensure_list_page(self, context, current_page, target_entry_url: str):
        if current_page is not None and not current_page.is_closed() and await self._is_probable_list_page(
            current_page,
            target_entry_url,
        ):
            return current_page
        list_pages = await self._collect_list_pages(context, target_entry_url)
        if list_pages:
            return list_pages[0]
        if current_page is not None and not current_page.is_closed():
            with contextlib.suppress(Exception):
                await current_page.goto(target_entry_url, wait_until="domcontentloaded")
                await current_page.wait_for_timeout(1200)
                if await self._is_probable_list_page(current_page, target_entry_url):
                    return current_page
        raise RuntimeError("鱼泡岗位列表页已被关闭，无法继续执行")

    async def _is_probable_list_page(self, page, target_entry_url: str) -> bool:
        page_url = self._safe_page_url(page)
        fallback_prefixes = (
            target_entry_url,
            settings.yupao_entry_url,
            "https://www.yupao.com/zhaogong/",
        )
        if any(page_url.startswith(prefix) for prefix in fallback_prefixes if prefix):
            return True
        with contextlib.suppress(Exception):
            if await page.locator(self.selectors.job_card).count():
                return True
        return False

    async def extract_job_payload(self, detail_page, detail_url: str) -> dict:
        info = await detail_page.evaluate(
            """() => {
                const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                const actionPattern = /发送简历|免费聊|继续聊|继续沟通|投递简历|发简历/;
                const title = normalize(document.querySelector('h1')?.textContent || '');
                const unique = (items) => {
                    const seen = new Set();
                    const values = [];
                    for (const item of items) {
                        const text = normalize(item);
                        if (!text || seen.has(text)) continue;
                        seen.add(text);
                        values.push(text);
                    }
                    return values;
                };
                let summaryRoot = document.querySelector('h1');
                let summaryFallback = summaryRoot || document.body;
                let summaryCursor = summaryRoot;
                for (let index = 0; index < 7 && summaryCursor; index += 1) {
                    const text = normalize(summaryCursor.innerText || summaryCursor.textContent || '');
                    if (text && text.length <= 900) {
                        summaryFallback = summaryCursor;
                    }
                    if (title && text.includes(title) && actionPattern.test(text) && text.length <= 1200) {
                        summaryFallback = summaryCursor;
                        break;
                    }
                    summaryCursor = summaryCursor.parentElement;
                }
                summaryRoot = summaryFallback || document.body;
                const summaryTexts = unique(
                    Array.from(summaryRoot.querySelectorAll('span, p, a, div, li, strong, em'))
                        .map((node) => normalize(node.textContent))
                        .filter(Boolean)
                ).filter((item) => item.length <= 80);
                const summaryText = normalize(summaryRoot.innerText || summaryRoot.textContent || '');
                let detailRoot = document.querySelector('h1');
                while (detailRoot && detailRoot.parentElement) {
                    const text = normalize(detailRoot.innerText || detailRoot.textContent || '');
                    if (text.includes('职位详情') || text.includes('工作地址') || text.includes('发送简历') || text.includes('免费聊')) {
                        break;
                    }
                    detailRoot = detailRoot.parentElement;
                }
                detailRoot = detailRoot || document.querySelector('main') || document.body;
                const detailText = (detailRoot.innerText || detailRoot.textContent || '').replace(/\\r/g, '').trim();
                const salary = summaryTexts.find((item) => /(万元\\/月|元\\/月|元\\/天|千\\/月|K\\/月|k\\/月|K|k|面议)/.test(item) && item.length <= 24) || '';
                const companyLinks = unique(
                    Array.from(summaryRoot.querySelectorAll('a[href*="/qiye/"]'))
                        .map((node) => normalize(node.textContent))
                        .filter((item) => item && item !== '查看企业信息')
                );
                const recruiterBlock = summaryTexts.find((item) => /(先生|女士)/.test(item) && /(活跃|在线)/.test(item)) || '';
                const recruiterMatch = recruiterBlock.match(/([^\\s|]+(?:先生|女士))/);
                const statusMatch = recruiterBlock.match(/(今日活跃|昨日活跃|当前未在线|在线|刚刚活跃)/);
                const recruiter = recruiterMatch ? recruiterMatch[1] : '';
                const recruiterStatus = statusMatch ? statusMatch[1] : '';
                let company = companyLinks[0] || '';
                if (!company && recruiterBlock.includes('|')) {
                    const parts = recruiterBlock.split('|').map(normalize).filter(Boolean);
                    company = parts.find((item) => item && !/(今日活跃|昨日活跃|在线|IP[:：])/.test(item)) || '';
                }
                const addressLink = Array.from(detailRoot.querySelectorAll('a[href*="/y/"]'))
                    .map((node) => normalize((node.textContent || '').replace(/^/, '')))
                    .find(Boolean) || '';
                let city = addressLink;
                if (!city) {
                    const marker = '工作地址：';
                    const idx = detailText.indexOf(marker);
                    if (idx !== -1) {
                        const nextLine = detailText
                            .slice(idx + marker.length)
                            .split('\\n')
                            .map((item) => normalize(item.replace(/^/, '')))
                            .find(Boolean) || '';
                        city = nextLine;
                    }
                }
                const startMarkers = ['岗位职责：', '职位详情：', '核心工作内容：', '岗位基本条件：', '任职要求：'];
                const endMarkers = ['职位亮点', '工作地址：', '查看企业信息', '查看更多信息', '热门职位', '相同职位推荐', '附近招聘', '职位大全'];
                let startIndex = -1;
                for (const marker of startMarkers) {
                    const idx = detailText.indexOf(marker);
                    if (idx !== -1 && (startIndex === -1 || idx < startIndex)) {
                        startIndex = idx;
                    }
                }
                let description = detailText;
                if (startIndex !== -1) {
                    let endIndex = detailText.length;
                    for (const marker of endMarkers) {
                        const idx = detailText.indexOf(marker, startIndex + 1);
                        if (idx !== -1 && idx < endIndex) {
                            endIndex = idx;
                        }
                    }
                    description = detailText.slice(startIndex, endIndex);
                }
                const tags = unique(
                    summaryTexts.filter((item) => {
                        if (!item) return false;
                        if (item.length > 24) return false;
                        if ([title, salary, company, city, recruiter, recruiterStatus].includes(item)) return false;
                        if (/发送简历|免费聊|继续聊|继续沟通|不合适|投诉|职位详情|工作地址|查看企业信息|查看更多信息|热门职位|相同职位推荐|附近招聘|职位大全|点击查看地图|法定代表人|成立日期|手机端|下载|按Enter键发送|换电话|换微信|发简历/.test(item)) {
                            return false;
                        }
                        if (/^IP[:：]/.test(item) || /^该职位于/.test(item)) return false;
                        return true;
                    })
                );
                return {
                    title,
                    salary,
                    company,
                    description,
                    recruiter,
                    recruiterStatus,
                    city,
                    tags,
                    summaryTexts,
                    summaryText,
                };
            }"""
        )
        summary_texts = self._normalize_text_list(info.get("summaryTexts", []))
        summary_text = self._normalize_text(str(info.get("summaryText") or ""))
        company_name = self._pick_company_name(
            self._normalize_text_list([info.get("company")]),
            summary_texts,
        )
        salary_text = self._extract_salary_text(summary_texts, str(info.get("salary") or ""), summary_text)
        salary_min, salary_max = self.normalize_salary(salary_text)
        experience_text = self._extract_experience_text(summary_texts, summary_text)
        education_text = self._extract_education_text(summary_texts, summary_text)
        city = self._normalize_text(str(info.get("city") or ""))
        recruiter_name = self._normalize_text(str(info.get("recruiter") or ""))
        recruiter_status = self._normalize_text(str(info.get("recruiterStatus") or ""))
        tags = self._merge_tags(
            info.get("tags", []),
            self._filter_short_tags(
                summary_texts,
                {self._normalize_text(str(info.get("title") or "")), salary_text, company_name, city, experience_text, education_text, recruiter_name, recruiter_status},
            ),
        )
        return {
            "platform_job_id": self.extract_job_code(detail_url),
            "title": self._normalize_text(str(info.get("title") or "")),
            "company_name": company_name,
            "salary_text": salary_text,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "description": self._normalize_multiline_text(str(info.get("description") or "")),
            "detail_url": detail_url,
            "tags": tags,
            "city": city,
            "experience_text": experience_text,
            "education_text": education_text,
            "recruiter_name": recruiter_name,
            "recruiter_status": recruiter_status,
        }

    @staticmethod
    def _build_job_key(job_payload: dict, *, fallback_index: int) -> str:
        for key in (
            job_payload.get("platform_job_id"),
            job_payload.get("detail_url"),
            f"{job_payload.get('title', '')}|{job_payload.get('company_name', '')}",
        ):
            normalized = " ".join(str(key or "").strip().split())
            if normalized:
                return normalized.lower()
        return f"fallback-{fallback_index}"

    def _merge_job_payload(self, list_payload: dict, detail_payload: dict) -> dict:
        merged = dict(list_payload)
        for key, value in detail_payload.items():
            if key == "tags":
                merged["tags"] = self._merge_tags(merged.get("tags", []), value if isinstance(value, list) else [])
                continue
            if key in {"salary_min", "salary_max"}:
                if value is not None:
                    merged[key] = value
                continue
            selected = self._pick_merged_value(merged.get(key), value)
            if selected is not None:
                merged[key] = selected

        salary_text = self._normalize_text(str(merged.get("salary_text") or ""))
        merged["salary_text"] = salary_text
        salary_min, salary_max = self.normalize_salary(salary_text)
        if salary_min is not None:
            merged["salary_min"] = salary_min
        if salary_max is not None:
            merged["salary_max"] = salary_max
        return merged

    def _pick_merged_value(self, base_value, detail_value):
        placeholders = {"", "公司", "企业", "未识别公司", "未命名岗位", "未标注", "未识别职位"}
        if isinstance(detail_value, str):
            normalized = self._normalize_text(detail_value)
            if normalized and normalized not in placeholders:
                return normalized
            return base_value
        if detail_value is None:
            return base_value
        return detail_value

    def _merge_tags(self, *tag_groups) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in tag_groups:
            if not isinstance(group, list):
                continue
            for item in group:
                text = self._normalize_text(str(item))
                if not text:
                    continue
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                merged.append(text)
        return merged[:12]

    async def go_to_next_page(
        self,
        list_page,
        should_stop: Callable[[], bool],
        status_callback: Callable[[str], None] | None = None,
        *,
        previous_count: int | None = None,
    ) -> bool:
        baseline_count = previous_count
        if baseline_count is None:
            with contextlib.suppress(Exception):
                baseline_count = await list_page.locator(self.selectors.job_card).count()
        baseline_count = baseline_count or 0
        baseline_signature = await self._read_page_signature(list_page)

        for attempt in range(6):
            await list_page.evaluate(
                """() => {
                    const doc = document.documentElement || document.body;
                    const height = Math.max(
                        doc?.scrollHeight || 0,
                        document.body?.scrollHeight || 0,
                        document.documentElement?.scrollHeight || 0
                    );
                    window.scrollTo({ top: height, behavior: 'instant' });
                }"""
            )
            if await self._sleep_or_stop(1.0 + attempt * 0.2, should_stop):
                return False

            current_count = baseline_count
            with contextlib.suppress(Exception):
                current_count = await list_page.locator(self.selectors.job_card).count()
            current_signature = await self._read_page_signature(list_page)

            if current_count > baseline_count or (current_signature and current_signature != baseline_signature):
                if status_callback and current_count > baseline_count:
                    status_callback(f"鱼泡列表已加载更多岗位：{baseline_count} -> {current_count}")
                return True

            if await self._page_contains_text(list_page, ("没有更多", "暂无更多", "已经到底")):
                if status_callback:
                    status_callback("鱼泡列表已滚动到底，没有更多岗位。")
                return False

        if status_callback:
            status_callback("鱼泡列表滚动后未出现新的岗位卡片。")
        return False

    async def _page_contains_text(self, page, markers: tuple[str, ...]) -> bool:
        with contextlib.suppress(Exception):
            body_text = await page.locator("body").inner_text()
            return any(marker in body_text for marker in markers)
        return False

    async def _detect_chat_page(self, page) -> bool:
        current_url = self._safe_page_url(page).lower()
        if "/chat/" in current_url:
            return True
        for selector in self.selectors.chat_root_candidates:
            locator = page.locator(selector).first
            with contextlib.suppress(Exception):
                if await locator.count() and await locator.is_visible():
                    return True
        with contextlib.suppress(Exception):
            body_text = await page.locator("body").inner_text()
            if "按Enter键发送" in body_text and "发送" in body_text:
                return True
        return False

    async def _detect_new_chat_pages(self, context, baseline_page_ids: set[int]) -> dict:
        for page in list(context.pages):
            if page.is_closed() or id(page) in baseline_page_ids:
                continue
            page_url = self._safe_page_url(page).lower()
            if "/chat/" in page_url or await self._detect_chat_page(page):
                return {
                    "success": True,
                    "reason": f"成功检测：鱼泡沟通流程打开了聊天页 {page_url}",
                    "chat_page": page,
                }
        return {"success": False, "reason": ""}

    async def _resolve_chat_page(self, context=None, *, preferred_page=None, fallback_page=None):
        candidates = [preferred_page]
        if context is not None:
            candidates.extend(reversed(list(context.pages)))
        candidates.append(fallback_page)
        seen: set[int] = set()

        for page in candidates:
            if page is None or page.is_closed():
                continue
            page_id = id(page)
            if page_id in seen:
                continue
            seen.add(page_id)
            with contextlib.suppress(Exception):
                if await self._detect_chat_page(page):
                    return page

        for page in candidates:
            if page is None or page.is_closed():
                continue
            page_id = id(page)
            if page_id in seen:
                continue
            seen.add(page_id)
            page_url = self._safe_page_url(page).lower()
            if "/chat/" in page_url:
                return page
        return None

    @staticmethod
    def _chat_page_ready_for_send(state: dict | None) -> bool:
        if not state:
            return False
        editors = state.get("editors") or []
        send_buttons = state.get("send_buttons") or []
        return bool(editors or send_buttons)

    async def _describe_chat_page_state(self, chat_page) -> dict:
        if chat_page is None or chat_page.is_closed():
            return {
                "url": "<closed>",
                "title": "",
                "editors": [],
                "send_buttons": [],
                "selected_threads": [],
                "body_excerpt": "",
            }

        try:
            state = await chat_page.evaluate(
                """() => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (el) => {
                        if (!(el instanceof HTMLElement)) return false;
                        const style = window.getComputedStyle(el);
                        if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const describe = (el) => ({
                        tag: el.tagName,
                        className: el.className || '',
                        text: normalize(el.innerText || el.textContent || ''),
                        placeholder: el.getAttribute('placeholder') || '',
                        disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
                        contenteditable: el.getAttribute('contenteditable') || '',
                    });
                    const editors = Array.from(document.querySelectorAll('div.fb-editor[data-fish-editor="true"], [contenteditable="true"], textarea, input[type="text"]'))
                        .filter(isVisible)
                        .map(describe)
                        .slice(0, 6);
                    const sendButtons = Array.from(document.querySelectorAll('button, [role="button"], a'))
                        .filter((el) => isVisible(el) && /发送/.test(normalize(el.innerText || el.textContent || '')))
                        .map(describe)
                        .slice(0, 6);
                    const selectedThreads = Array.from(document.querySelectorAll('div.addga.addgc, div.addga[class*="addgc"]'))
                        .filter(isVisible)
                        .map(describe)
                        .slice(0, 3);
                    return {
                        url: location.href,
                        title: document.title || '',
                        editors,
                        sendButtons,
                        selectedThreads,
                        bodyExcerpt: normalize(document.body?.innerText || '').slice(0, 300),
                    };
                }"""
            )
        except Exception:
            logger.warning("Yupao describe chat page failed.", exc_info=True)
            return {
                "url": self._safe_page_url(chat_page),
                "title": "",
                "editors": [],
                "send_buttons": [],
                "selected_threads": [],
                "body_excerpt": "",
            }

        return {
            "url": self._normalize_text(str(state.get("url") or self._safe_page_url(chat_page))),
            "title": self._normalize_text(str(state.get("title") or "")),
            "editors": state.get("editors", []) if isinstance(state, dict) else [],
            "send_buttons": state.get("sendButtons", []) if isinstance(state, dict) else [],
            "selected_threads": state.get("selectedThreads", []) if isinstance(state, dict) else [],
            "body_excerpt": self._normalize_text(str(state.get("bodyExcerpt") or "")) if isinstance(state, dict) else "",
        }

    def _format_chat_page_state(self, state: dict | None) -> str:
        if not state:
            return "chat_state=<unknown>"
        editors = state.get("editors") or []
        send_buttons = state.get("send_buttons") or []
        selected_threads = state.get("selected_threads") or []
        editor_summary = ", ".join(
            self._normalize_text(str(item.get("text") or item.get("placeholder") or item.get("className") or item.get("tag") or ""))
            for item in editors
        ) or "<none>"
        send_summary = ", ".join(
            self._normalize_text(str(item.get("text") or item.get("className") or item.get("tag") or ""))
            for item in send_buttons
        ) or "<none>"
        selected_summary = ", ".join(
            self._normalize_text(str(item.get("text") or item.get("className") or item.get("tag") or ""))
            for item in selected_threads
        ) or "<none>"
        url = self._normalize_text(str(state.get("url") or ""))
        title = self._normalize_text(str(state.get("title") or ""))
        parts = []
        if url:
            parts.append(f"url={url}")
        if title:
            parts.append(f"title={title}")
        parts.append(f"selected={selected_summary}")
        parts.append(f"editors={editor_summary}")
        parts.append(f"send_buttons={send_summary}")
        body_excerpt = self._normalize_text(str(state.get("body_excerpt") or ""))
        if body_excerpt:
            parts.append(f"body={body_excerpt[:120]}")
        return " | ".join(parts)

    async def _extract_chat_message_context(self, detail_page, chat_page) -> tuple[str, str]:
        target_page = detail_page or chat_page
        if target_page is None:
            return "", ""
        try:
            info = await target_page.evaluate(
                """() => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const title = normalize(document.querySelector('h1')?.textContent || document.title || '');
                    const company = normalize(document.querySelector('a[href*="/qiye/"]')?.textContent || '');
                    return { title, company };
                }"""
            )
        except Exception:
            logger.warning("Yupao extract chat message context failed.", exc_info=True)
            return "", ""
        title = self._normalize_text(str(info.get("title") or ""))
        company = self._normalize_text(str(info.get("company") or ""))
        if title:
            title = title.split("_")[0].split("-")[0].strip()
        return title, company

    async def _open_chat_thread_from_resume_notice(
        self,
        chat_page,
        should_stop: Callable[[], bool],
        *,
        detail_page=None,
    ) -> tuple[bool, str]:
        if chat_page is None or chat_page.is_closed():
            return False, "鱼泡聊天页已关闭"
        expected_title, expected_company = await self._extract_chat_message_context(detail_page, chat_page)
        allow_generic_fallback = not (expected_title or expected_company)
        try:
            result = await chat_page.evaluate(
                """(jobTitle, companyName, allowGenericFallback) => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (el) => {
                        if (!(el instanceof HTMLElement)) return false;
                        const style = window.getComputedStyle(el);
                        if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const compact = (value) => normalize(value).replace(/[\\s|丨/\\\\,，、·\\-（）()【】\\[\\]]+/g, '');
                    const buildFragments = (value) => {
                        const source = compact(value);
                        const fragments = new Set();
                        if (!source) return [];
                        fragments.add(source);
                        for (const size of [4, 3, 2]) {
                            if (source.length < size) continue;
                            for (let index = 0; index <= source.length - size; index += 1) {
                                fragments.add(source.slice(index, index + size));
                            }
                        }
                        return Array.from(fragments).filter((item) => item.length >= 2);
                    };
                    const dispatchClick = (target) => {
                        target.scrollIntoView({ block: 'center', inline: 'nearest' });
                        for (const type of ['mousedown', 'mouseup', 'click']) {
                            target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, composed: true, view: window }));
                        }
                    };
                    const noticePatterns = [
                        '你的附件简历已经发送给老板',
                        '附件简历已经发送给老板',
                    ];
                    const titleFragments = buildFragments(jobTitle);
                    const normalizedCompany = normalize(companyName);
                    const rows = Array.from(document.querySelectorAll('div.addga'))
                        .filter(isVisible)
                        .map((row) => {
                            const text = normalize(row.innerText || row.textContent || '');
                            const hasNotice = noticePatterns.some((pattern) => text.includes(pattern));
                            const selected = /(^|\\s)addgc(\\s|$)/.test(row.className || '');
                            let score = 0;
                            let matchedCompany = false;
                            let matchedTitle = false;
                            if (normalizedCompany && text.includes(normalizedCompany)) {
                                score += 100;
                                matchedCompany = true;
                            }
                            if (jobTitle && (text.includes(jobTitle) || jobTitle.includes(text))) {
                                score += 60;
                                matchedTitle = true;
                            }
                            let fragmentHits = 0;
                            for (const fragment of titleFragments) {
                                if (fragment.length >= 2 && text.includes(fragment)) {
                                    fragmentHits += 1;
                                }
                            }
                            if (fragmentHits) {
                                score += Math.min(fragmentHits, 6) * 8;
                                matchedTitle = true;
                            }
                            if (hasNotice) {
                                score += 12;
                            }
                            if (selected) {
                                score += 4;
                            }
                            return {
                                row,
                                text,
                                hasNotice,
                                selected,
                                matchedCompany,
                                matchedTitle,
                                score,
                            };
                        });
                    const selectedMatch = rows.find((item) => item.selected && (
                        (normalizedCompany && item.matchedCompany)
                        || (!normalizedCompany && item.matchedTitle)
                        || (allowGenericFallback && item.hasNotice)
                    ));
                    if (selectedMatch) {
                        return {
                            clicked: true,
                            alreadyActive: true,
                            text: selectedMatch.text,
                            targetText: selectedMatch.text,
                            expectedCompany: normalizedCompany,
                            expectedTitle: normalize(jobTitle),
                            matchedCompany: selectedMatch.matchedCompany,
                            matchedTitle: selectedMatch.matchedTitle,
                        };
                    }
                    let candidates = rows.filter((item) => item.hasNotice || item.matchedCompany || item.matchedTitle);
                    if (normalizedCompany || jobTitle) {
                        candidates = candidates.filter((item) => item.matchedCompany || item.matchedTitle);
                    }
                    if (!candidates.length && allowGenericFallback) {
                        candidates = rows.filter((item) => item.hasNotice);
                    }
                    candidates.sort((left, right) => right.score - left.score);
                    const target = candidates[0];
                    if (!target) {
                        return {
                            clicked: false,
                            alreadyActive: false,
                            text: '',
                            targetText: '',
                            expectedCompany: normalizedCompany,
                            expectedTitle: normalize(jobTitle),
                            sample: rows.slice(0, 5).map((item) => item.text),
                        };
                    }
                    dispatchClick(target.row);
                    return {
                        clicked: true,
                        alreadyActive: false,
                        text: target.text,
                        targetText: target.text,
                        expectedCompany: normalizedCompany,
                        expectedTitle: normalize(jobTitle),
                        matchedCompany: target.matchedCompany,
                        matchedTitle: target.matchedTitle,
                    };
                }""",
                expected_title,
                expected_company,
                allow_generic_fallback,
            )
        except Exception:
            logger.warning("Yupao resume notice thread activation failed.", exc_info=True)
            return False, "点击鱼泡“附件简历已发送”会话失败"

        if not bool(result.get("clicked", False)):
            state = await self._describe_chat_page_state(chat_page)
            expected_company_text = self._normalize_text(str(result.get("expectedCompany") or expected_company or ""))
            expected_title_text = self._normalize_text(str(result.get("expectedTitle") or expected_title or ""))
            target_hint = " / ".join(part for part in (expected_company_text, expected_title_text) if part)
            if target_hint:
                target_hint = f"目标会话={target_hint} | "
            return False, f"未找到鱼泡目标会话 | {target_hint}{self._format_chat_page_state(state)}"

        deadline = time.time() + 5
        action_summary = "已定位到鱼泡目标会话" if bool(result.get("alreadyActive", False)) else "已点击鱼泡目标会话"
        last_summary = f"{action_summary}，等待输入框出现"
        while time.time() < deadline:
            if should_stop():
                return False, "收到停止指令，中断鱼泡会话激活"
            textarea = await self._find_first_visible(
                chat_page,
                self.selectors.chat_textarea_candidates,
                should_stop,
                timeout_ms=400,
            )
            if textarea is not None:
                return True, f"已切换到鱼泡聊天会话：{self._normalize_text(str(result.get('targetText') or result.get('text') or ''))}"
            state = await self._describe_chat_page_state(chat_page)
            last_summary = self._format_chat_page_state(state)
            await self._sleep_or_stop(0.25, should_stop)
        return False, f"{action_summary}后仍未出现输入框 | {last_summary}"

    async def _activate_chat_conversation(
        self,
        detail_page,
        should_stop: Callable[[], bool],
        *,
        context=None,
        fallback_chat_page=None,
    ) -> tuple[object | None, str]:
        if detail_page is None or detail_page.is_closed():
            return None, "详情页已关闭，无法继续发起鱼泡聊天"

        trigger = await self._find_first_visible(
            detail_page,
            self.selectors.continue_chat_button_candidates + self.selectors.chat_button_candidates,
            should_stop,
            timeout_ms=5000,
        )
        if trigger is None:
            return None, "投递成功后未找到鱼泡继续聊入口"

        with contextlib.suppress(Exception):
            await detail_page.bring_to_front()
        with contextlib.suppress(Exception):
            await trigger.scroll_into_view_if_needed()
        with contextlib.suppress(Exception):
            await trigger.hover()

        trigger_text = ""
        with contextlib.suppress(Exception):
            trigger_text = self._normalize_text(await trigger.inner_text())

        clicked = False
        for click_mode in ("normal", "script"):
            try:
                if click_mode == "normal":
                    await trigger.click(timeout=4000, no_wait_after=True)
                else:
                    await trigger.evaluate("(el) => el.click()")
                clicked = True
                logger.info("Yupao chat trigger clicked: mode=%s text=%s", click_mode, trigger_text)
                break
            except Exception:
                logger.warning("Yupao chat trigger click failed: mode=%s text=%s", click_mode, trigger_text, exc_info=True)

        if not clicked:
            return None, f"鱼泡继续聊入口点击失败: {trigger_text or '<unknown>'}"

        deadline = time.time() + 8
        last_summary = "鱼泡继续聊入口已点击，但尚未进入可发送消息的会话"
        chat_thread_checked = False
        while time.time() < deadline:
            if should_stop():
                return None, "收到停止指令，中断鱼泡聊天会话等待"
            if context is not None:
                for page in reversed(list(context.pages)):
                    if page.is_closed():
                        continue
                    with contextlib.suppress(Exception):
                        if not await self._detect_chat_page(page):
                            continue
                    state = await self._describe_chat_page_state(page)
                    last_summary = self._format_chat_page_state(state)
                    if self._chat_page_ready_for_send(state):
                        return page, f"已进入鱼泡聊天会话 | {last_summary}"

            if fallback_chat_page is not None and not fallback_chat_page.is_closed():
                if not chat_thread_checked:
                    activated, activation_summary = await self._open_chat_thread_from_resume_notice(
                        fallback_chat_page,
                        should_stop,
                        detail_page=detail_page,
                    )
                    last_summary = self._combine_delivery_reason(last_summary, activation_summary)
                    chat_thread_checked = activated
                    if activated:
                        return fallback_chat_page, activation_summary
                state = await self._describe_chat_page_state(fallback_chat_page)
                last_summary = self._format_chat_page_state(state)
                if self._chat_page_ready_for_send(state):
                    return fallback_chat_page, f"已进入鱼泡聊天会话 | {last_summary}"

            await self._sleep_or_stop(0.3, should_stop)

        return None, f"点击继续聊后仍未进入鱼泡聊天会话 | {last_summary}"

    async def detect_apply_success(self, detail_page, *, before_state: dict | None = None) -> dict:
        state = await self._inspect_delivery_state(detail_page)
        if state.get("login_required", False):
            return {"success": False, "login_required": True, "reason": str(state.get("reason") or "鱼泡流程跳转到了登录页，请重新登录账号")}

        for selector in self.selectors.apply_success_candidates:
            locator = detail_page.locator(selector).first
            with contextlib.suppress(Exception):
                if await locator.count() and await locator.is_visible():
                    return {
                        "success": True,
                        "already_delivered": False,
                        "reason": f"成功检测：页面出现鱼泡投递成功提示 ({selector})",
                        "state": state,
                    }

        if state.get("has_success_toast", False):
            return {
                "success": True,
                "already_delivered": False,
                "reason": f"成功检测：页面出现鱼泡投递成功弹窗 | {self._format_delivery_state(state)}",
                "state": state,
            }

        if before_state and before_state.get("has_apply_button", False):
            if not state.get("has_apply_button", False) and state.get("has_continue_chat", False):
                return {
                    "success": True,
                    "already_delivered": False,
                    "reason": f"成功检测：发送简历按钮已消失，仅剩继续聊 | {self._format_delivery_state(state)}",
                    "state": state,
                }

        if state.get("already_delivered", False):
            return {
                "success": False,
                "already_delivered": True,
                "reason": f"点击前检测：当前岗位仅剩继续聊，说明之前已投递过 | {self._format_delivery_state(state)}",
                "state": state,
            }

        return {
            "success": False,
            "already_delivered": False,
            "reason": f"未检测到鱼泡投递成功状态 | {self._format_delivery_state(state)}",
            "state": state,
        }

    async def detect_delivery_success(self, detail_page, *, before_state: dict | None = None) -> dict:
        return await self.detect_apply_success(detail_page, before_state=before_state)

    async def try_deliver(
        self,
        detail_page,
        should_stop: Callable[[], bool],
        *,
        context=None,
        baseline_page_ids: set[int] | None = None,
        chat_page=None,
    ) -> dict:
        pre_detect = await self.detect_apply_success(detail_page)
        if pre_detect.get("already_delivered", False):
            return {
                "success": False,
                "already_delivered": True,
                "reason": str(pre_detect.get("reason", "当前岗位仅剩继续聊，说明之前已投递过")),
                "chat_sent": False,
                "chat_message": "",
                "send_status": "skipped",
            }
        if pre_detect.get("login_required", False):
            return {
                "success": False,
                "already_delivered": False,
                "reason": pre_detect["reason"],
                "chat_sent": False,
                "chat_message": "",
                "send_status": "skipped",
            }
        if pre_detect.get("success", False):
            return {
                "success": True,
                "already_delivered": False,
                "reason": str(pre_detect.get("reason", "当前岗位已完成鱼泡投递")),
                "chat_sent": False,
                "chat_message": "",
                "send_status": "skipped",
            }

        before_state = pre_detect.get("state") or await self._inspect_delivery_state(detail_page)

        button = await self._find_first_visible(
            detail_page,
            self.selectors.apply_button_candidates,
            should_stop,
            timeout_ms=8000,
        )
        if button is None:
            return {
                "success": False,
                "already_delivered": bool(before_state.get("already_delivered", False)),
                "reason": f"未找到可点击的鱼泡发送简历按钮 | {self._format_delivery_state(before_state)}",
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
                logger.warning("Yupao %s apply click failed.", click_mode, exc_info=True)
                continue

            modal_result = await self._wait_for_apply_modal_or_success(
                detail_page,
                should_stop,
                before_state=before_state,
                context=context,
                baseline_page_ids=baseline_page_ids,
            )
            if modal_result.get("login_required", False):
                return {
                    "success": False,
                    "already_delivered": False,
                    "reason": f"点击方式：{click_mode} | {modal_result['reason']}",
                    "chat_sent": False,
                    "chat_message": "",
                    "send_status": "skipped",
                }

            if modal_result.get("success", False):
                return await self._finalize_delivery_result(
                    modal_result,
                    click_mode=click_mode,
                    should_stop=should_stop,
                    context=context,
                    fallback_chat_page=chat_page,
                )

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
                    "already_delivered": False,
                    "reason": f"点击方式：{click_mode} | 已打开鱼泡确认弹窗，但未找到确认按钮",
                    "chat_sent": False,
                    "chat_message": "",
                    "send_status": "skipped",
                }

            try:
                await confirm_button.click(timeout=5000)
            except Exception:
                logger.warning("Yupao apply confirm click failed.", exc_info=True)
                continue

            delivery_result = await self._wait_for_apply_success(
                detail_page,
                should_stop,
                before_state=before_state,
                context=context,
                baseline_page_ids=baseline_page_ids,
            )
            if delivery_result.get("login_required", False):
                return {
                    "success": False,
                    "already_delivered": False,
                    "reason": f"点击方式：{click_mode} | {delivery_result['reason']}",
                    "chat_sent": False,
                    "chat_message": "",
                    "send_status": "skipped",
                }
            if delivery_result.get("success", False):
                return await self._finalize_delivery_result(
                    delivery_result,
                    click_mode=click_mode,
                    should_stop=should_stop,
                    context=context,
                    fallback_chat_page=chat_page,
                )

        return {
            "success": False,
            "already_delivered": False,
            "reason": f"点击鱼泡发送简历按钮后未检测到成功标识 | {self._format_delivery_state(await self._inspect_delivery_state(detail_page))}",
            "chat_sent": False,
            "chat_message": "",
            "send_status": self._default_send_status(),
        }

    async def _finalize_delivery_result(
        self,
        delivery_result: dict,
        *,
        click_mode: str,
        should_stop: Callable[[], bool],
        context=None,
        fallback_chat_page=None,
    ) -> dict:
        chat_page = delivery_result.get("chat_page")
        detail_page = delivery_result.get("detail_page")
        chat_sent = False
        chat_message = ""
        send_status = self._default_send_status()
        chat_summary = ""

        if delivery_result.get("success", False) and not delivery_result.get("already_delivered", False) and self._chat_actions_enabled():
            target_chat_page = await self._resolve_chat_page(
                context,
                preferred_page=chat_page,
                fallback_page=fallback_chat_page,
            )
            target_chat_confirmed = False
            if target_chat_page is not None:
                activated, activation_summary = await self._open_chat_thread_from_resume_notice(
                    target_chat_page,
                    should_stop,
                    detail_page=detail_page,
                )
                chat_summary = self._combine_delivery_reason(chat_summary, activation_summary)
                target_chat_confirmed = activated
                if not activated:
                    target_chat_state = await self._describe_chat_page_state(target_chat_page)
                    logger.info(
                        "Yupao chat page not targeted yet, falling back to continue-chat trigger: %s",
                        self._format_chat_page_state(target_chat_state),
                    )
            if target_chat_page is None and detail_page is not None and await self._detect_chat_page(detail_page):
                detail_state = await self._describe_chat_page_state(detail_page)
                if self._chat_page_ready_for_send(detail_state):
                    target_chat_page = detail_page
                    target_chat_confirmed = True
            if target_chat_page is None or not target_chat_confirmed:
                activated_page, activation_summary = await self._activate_chat_conversation(
                    detail_page,
                    should_stop,
                    context=context,
                    fallback_chat_page=fallback_chat_page,
                )
                if activated_page is not None:
                    target_chat_page = activated_page
                    target_chat_confirmed = True
                chat_summary = self._combine_delivery_reason(chat_summary, activation_summary)
            if target_chat_page is not None:
                chat_page = target_chat_page
                chat_result = await self._run_chat_actions(
                    target_chat_page,
                    should_stop,
                    detail_page=detail_page,
                )
                chat_sent = bool(chat_result.get("sent", False))
                chat_message = str(chat_result.get("message", "") or "")
                send_status = str(chat_result.get("send_status", self._default_send_status()))
                chat_summary = self._combine_delivery_reason(chat_summary, str(chat_result.get("status_summary", "") or "").strip())
                logger.info(
                    "Yupao chat action result: page=%s send_status=%s sent=%s summary=%s",
                    self._safe_page_url(target_chat_page),
                    send_status,
                    chat_sent,
                    chat_summary,
                )
            else:
                chat_state = await self._describe_chat_page_state(fallback_chat_page)
                chat_summary = f"未定位到可发送消息的鱼泡聊天页 | {self._format_chat_page_state(chat_state)}"
                logger.warning("Yupao chat page unavailable after delivery: %s", chat_summary)

        return {
            "success": bool(delivery_result.get("success", False)),
            "already_delivered": bool(delivery_result.get("already_delivered", False)),
            "reason": self._combine_delivery_reason(
                f"点击方式：{click_mode} | {delivery_result.get('reason', '鱼泡沟通流程完成')}",
                chat_summary,
            ),
            "chat_sent": chat_sent,
            "chat_message": chat_message,
            "send_status": send_status,
            "chat_page": chat_page,
        }

    async def _wait_for_apply_modal_or_success(
        self,
        detail_page,
        should_stop: Callable[[], bool],
        timeout_ms: int = 8000,
        *,
        before_state: dict | None = None,
        context=None,
        baseline_page_ids: set[int] | None = None,
    ) -> dict:
        deadline = time.time() + timeout_ms / 1000
        last_reason = "未检测到鱼泡弹窗或成功标识"
        while time.time() < deadline:
            if should_stop():
                return {"success": False, "reason": "收到停止指令，中断鱼泡结果等待"}

            with contextlib.suppress(Exception):
                await detail_page.wait_for_load_state("domcontentloaded", timeout=500)

            success_result = await self.detect_apply_success(detail_page, before_state=before_state)
            success_result["detail_page"] = detail_page
            if success_result.get("success", False):
                if context is not None and baseline_page_ids:
                    extra_page_result = await self._detect_new_chat_pages(context, baseline_page_ids)
                    if extra_page_result.get("success", False):
                        success_result["chat_page"] = extra_page_result.get("chat_page")
                return success_result
            if success_result.get("login_required", False):
                return success_result

            modal = detail_page.locator(self.selectors.apply_resume_modal).first
            with contextlib.suppress(Exception):
                if await modal.count() and await modal.is_visible():
                    modal_text = self._normalize_text(await modal.inner_text())
                    if any(token in modal_text for token in ("沟通", "联系", "确定", "简历", "发送")):
                        return {
                            "success": False,
                            "modal_visible": True,
                            "reason": f"成功检测：已打开鱼泡确认弹窗 {modal_text}",
                        }

            last_reason = success_result.get("reason", last_reason)
            await self._sleep_or_stop(0.25, should_stop)
        return {"success": False, "reason": last_reason}

    async def _wait_for_apply_success(
        self,
        detail_page,
        should_stop: Callable[[], bool],
        timeout_ms: int = 10000,
        *,
        before_state: dict | None = None,
        context=None,
        baseline_page_ids: set[int] | None = None,
    ) -> dict:
        deadline = time.time() + timeout_ms / 1000
        last_reason = "未检测到鱼泡投递成功状态"
        while time.time() < deadline:
            if should_stop():
                return {"success": False, "reason": "收到停止指令，中断鱼泡投递成功等待"}

            with contextlib.suppress(Exception):
                await detail_page.wait_for_load_state("domcontentloaded", timeout=600)

            result = await self.detect_apply_success(detail_page, before_state=before_state)
            result["detail_page"] = detail_page
            if result.get("success", False):
                if context is not None and baseline_page_ids:
                    extra_page_result = await self._detect_new_chat_pages(context, baseline_page_ids)
                    if extra_page_result.get("success", False):
                        result["chat_page"] = extra_page_result.get("chat_page")
                return result
            if result.get("login_required", False):
                return result

            last_reason = result.get("reason", last_reason)
            await self._sleep_or_stop(0.3, should_stop)
        return {"success": False, "reason": last_reason}

    async def _run_chat_actions(
        self,
        chat_page,
        should_stop: Callable[[], bool],
        *,
        detail_page=None,
    ) -> dict:
        text_result = await self._send_text_message(
            chat_page,
            should_stop,
            detail_page=detail_page or chat_page,
        )
        requested = bool(text_result.get("attempted", False))
        chat_sent = bool(text_result.get("sent", False))
        send_status = "skipped"
        if requested:
            send_status = "sent" if chat_sent else "failed"
        summary = str(text_result.get("summary", "") or "").strip()
        reason = str(text_result.get("reason", "") or "").strip()
        status_summary = "；".join(part for part in (summary, reason) if part)
        return {
            "sent": chat_sent,
            "message": str(text_result.get("chat_message", "") or ""),
            "send_status": send_status,
            "status_summary": status_summary,
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

        job_title, company_name = await self._extract_chat_message_context(detail_page, chat_page)
        message = self._build_chat_message(job_title, company_name)
        if not message:
            return {"attempted": False, "sent": False, "reason": "", "summary": "", "chat_message": ""}

        chat_state = await self._describe_chat_page_state(chat_page)
        logger.info("Yupao chat page resolved: %s", self._format_chat_page_state(chat_state))

        activated, activation_reason = await self._open_chat_thread_from_resume_notice(
            chat_page,
            should_stop,
            detail_page=detail_page,
        )
        if not activated:
            chat_state = await self._describe_chat_page_state(chat_page)
            return {
                "attempted": True,
                "sent": False,
                "reason": self._combine_delivery_reason(
                    activation_reason,
                    f"未能切换到鱼泡目标会话 | {self._format_chat_page_state(chat_state)}",
                ),
                "summary": "",
                "chat_message": message,
            }

        textarea = await self._find_first_visible(
            chat_page,
            self.selectors.chat_textarea_candidates,
            should_stop,
            timeout_ms=6000,
        )
        if textarea is None:
            logger.info("Yupao chat thread activated from resume notice: %s", activation_reason)
            chat_state = await self._describe_chat_page_state(chat_page)
            textarea = await self._find_first_visible(
                chat_page,
                self.selectors.chat_textarea_candidates,
                should_stop,
                timeout_ms=4000,
            )

        if textarea is None:
            chat_state = await self._describe_chat_page_state(chat_page)
            return {
                "attempted": True,
                "sent": False,
                "reason": f"鱼泡会话已激活，但仍未找到聊天输入框 | {self._format_chat_page_state(chat_state)}",
                "summary": "",
                "chat_message": message,
            }

        try:
            await textarea.click(timeout=3000)
            tag_name = await textarea.evaluate("(el) => el.tagName.toLowerCase()")
            is_contenteditable = await textarea.evaluate("(el) => el.getAttribute('contenteditable') === 'true'")
            if tag_name in {"textarea", "input"}:
                with contextlib.suppress(Exception):
                    await textarea.press("Control+A")
                await textarea.fill("")
                await textarea.fill(message)
            elif is_contenteditable:
                with contextlib.suppress(Exception):
                    await textarea.press("Control+A")
                with contextlib.suppress(Exception):
                    await textarea.press("Backspace")
                try:
                    await textarea.type(message, delay=20)
                except Exception:
                    await textarea.evaluate(
                        """(el, value) => {
                            el.focus();
                            el.innerHTML = '';
                            const paragraph = document.createElement('p');
                            paragraph.textContent = value;
                            el.appendChild(paragraph);
                            el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }""",
                        message,
                    )
            else:
                await textarea.evaluate("(el, value) => { el.textContent = value; }", message)
            await self._sleep_or_stop(0.3, should_stop)
        except Exception:
            logger.warning("Yupao chat message fill failed.", exc_info=True)
            return {
                "attempted": True,
                "sent": False,
                "reason": f"鱼泡聊天消息填写失败 | {self._format_chat_page_state(chat_state)}",
                "summary": "",
                "chat_message": message,
            }

        send_button = await self._find_first_visible(
            chat_page,
            self.selectors.chat_send_button_candidates,
            should_stop,
            timeout_ms=4000,
        )
        try:
            if send_button is not None:
                disabled = False
                with contextlib.suppress(Exception):
                    disabled = await send_button.is_disabled()
                if disabled:
                    raise RuntimeError("发送按钮当前不可点击")
                await send_button.click(timeout=3000)
            else:
                await textarea.press("Enter")
        except Exception:
            logger.warning("Yupao chat send click failed.", exc_info=True)
            current_state = await self._describe_chat_page_state(chat_page)
            return {
                "attempted": True,
                "sent": False,
                "reason": f"鱼泡聊天消息发送失败 | {self._format_chat_page_state(current_state)}",
                "summary": "",
                "chat_message": message,
            }

        echoed = await self._wait_for_message_echo(chat_page, message, should_stop, timeout_ms=5000)
        if not echoed:
            current_state = await self._describe_chat_page_state(chat_page)
            logger.warning("Yupao chat message echo not detected: %s", self._format_chat_page_state(current_state))
            return {
                "attempted": True,
                "sent": False,
                "reason": f"发送后未检测到鱼泡聊天消息回显 | {self._format_chat_page_state(current_state)}",
                "summary": "",
                "chat_message": message,
            }

        return {
            "attempted": True,
            "sent": True,
            "reason": "",
            "summary": "已发送追加消息",
            "chat_message": message,
        }

    def run_sync_mock(self, keyword, per_job_callback, should_stop) -> None:
        samples = [
            {
                "platform_job_id": "100001",
                "title": "土建施工员",
                "company_name": "鱼泡测试建筑队",
                "salary_text": "8千-1.2万",
                "salary_min": 8,
                "salary_max": 12,
                "description": "负责现场施工组织、进度跟进与班组协调。",
                "detail_url": "https://www.yupao.com/zhaogong/100001.html",
                "tags": ["包住", "工地", "施工"],
                "city": "广州",
                "experience_text": "1-3年",
                "education_text": "不限",
                "delivery_success": True,
                "status_reason": f"入口 {keyword or settings.yupao_entry_url} 命中，已完成模拟沟通。",
                "chat_sent": False,
                "chat_message": "",
                "send_status": "skipped",
            },
            {
                "platform_job_id": "100002",
                "title": "钢筋工",
                "company_name": "鱼泡测试劳务队",
                "salary_text": "320元/天",
                "salary_min": 320,
                "salary_max": 320,
                "description": "负责钢筋绑扎与现场施工配合。",
                "detail_url": "https://www.yupao.com/zhaogong/100002.html",
                "tags": ["日结", "包吃", "钢筋"],
                "city": "佛山",
                "experience_text": "经验不限",
                "education_text": "不限",
                "delivery_success": True,
                "status_reason": "模拟鱼泡岗位已完成沟通动作。",
                "chat_sent": False,
                "chat_message": "",
                "send_status": "skipped",
            },
        ]
        for sample in samples:
            if should_stop():
                break
            per_job_callback(sample)
