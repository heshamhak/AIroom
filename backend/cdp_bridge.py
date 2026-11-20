from __future__ import annotations

import asyncio
import os
import random
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import httpx
from playwright.async_api import async_playwright, Browser, Page, Playwright

from config import load_settings
from models import CdpStatusResponse, CdpTabInfo


class QuotaExhaustedError(RuntimeError):
    """Raised when a free-tier model hits its message/length/rate limit."""

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"[{provider}] انتهى رصيد المحادثة أو تم الوصول للحد المجاني: {reason}")


_QUOTA_PATTERNS = [
    "you've reached your",
    "you have reached your",
    "your message limit",
    "usage limit",
    "free plan limit",
    "upgrade to plus",
    "upgrade to pro",
    "too many requests",
    "length limit for this chat",
    "لقد وصلت إلى الحد",
    "تم الوصول إلى الحد",
]


def _classify_url(url: str) -> Literal["chatgpt", "claude", "other"]:
    lower = url.lower()
    if "chatgpt.com" in lower or "chat.openai.com" in lower:
        return "chatgpt"
    if "claude.ai" in lower:
        return "claude"
    return "other"


AIROOM_CHROME_DIR = load_settings().chrome_dir


def _read_devtools_active_port() -> str | None:
    """Read DevToolsActivePort from AIroom's dedicated Chrome profile first."""
    candidates = [
        AIROOM_CHROME_DIR / "DevToolsActivePort",
    ]
    if os.environ.get("AIROOM_ALLOW_DEFAULT_PROFILE") == "1":
        candidates.extend([
            Path.home() / ".config/google-chrome/DevToolsActivePort",
            Path.home() / ".config/chromium/DevToolsActivePort",
        ])
    for path in candidates:
        if path.exists():
            lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if len(lines) >= 2 and lines[0].isdigit() and lines[1].startswith("/devtools/"):
                return f"ws://127.0.0.1:{lines[0]}{lines[1]}"
    return None


def resolve_cdp_endpoint(cdp_url: str) -> str:
    """Prefer the user's real Chrome DevToolsActivePort WebSocket URL if available."""
    ws_url = _read_devtools_active_port()
    if ws_url:
        return ws_url
    return cdp_url.rstrip("/")


async def inspect_cdp(cdp_url: str) -> CdpStatusResponse:
    """Check if Chrome CDP is reachable (HTTP /json/version first, then DevToolsActivePort)."""
    base = cdp_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            ver_resp = await client.get(f"{base}/json/version")
            ver_resp.raise_for_status()
            ver_data = ver_resp.json()

            list_resp = await client.get(f"{base}/json/list")
            list_resp.raise_for_status()
            raw_tabs = list_resp.json()

        tabs: list[CdpTabInfo] = []
        chatgpt_ready = False
        claude_ready = False
        for t in raw_tabs:
            if t.get("type") != "page":
                continue
            url = t.get("url", "")
            role = _classify_url(url)
            if role == "chatgpt":
                chatgpt_ready = True
            elif role == "claude":
                claude_ready = True
            tabs.append(
                CdpTabInfo(
                    id=str(t.get("id", "")),
                    title=t.get("title", "Untitled"),
                    url=url,
                    role=role,
                )
            )
        return CdpStatusResponse(
            connected=True,
            cdp_url=cdp_url,
            browser=ver_data.get("Browser"),
            chatgpt_ready=chatgpt_ready,
            claude_ready=claude_ready,
            tabs=tabs,
        )
    except Exception as http_exc:
        ws_url = _read_devtools_active_port()
        if ws_url:
            try:
                async with async_playwright() as pw:
                    browser = await pw.chromium.connect_over_cdp(ws_url, timeout=4000)
                    tabs = []
                    chatgpt_ready = False
                    claude_ready = False
                    for ctx in browser.contexts:
                        for page in ctx.pages:
                            url = page.url
                            role = _classify_url(url)
                            if role == "chatgpt":
                                chatgpt_ready = True
                            elif role == "claude":
                                claude_ready = True
                            tabs.append(
                                CdpTabInfo(
                                    id=str(id(page)),
                                    title=await page.title(),
                                    url=url,
                                    role=role,
                                )
                            )
                    await browser.close()
                return CdpStatusResponse(
                    connected=True,
                    cdp_url=ws_url,
                    browser="Google Chrome",
                    chatgpt_ready=chatgpt_ready,
                    claude_ready=claude_ready,
                    tabs=tabs,
                )
            except Exception as ws_exc:
                return CdpStatusResponse(connected=False, cdp_url=cdp_url, error=str(ws_exc))
        return CdpStatusResponse(connected=False, cdp_url=cdp_url, error=str(http_exc))


def launch_chrome_cdp(
    port: int = 9223,
    user_data_dir: str | None = None,
    headless: bool = False,
) -> dict:
    """Launch AIroom's dedicated Chromium engine."""
    profile_dir = user_data_dir or str(load_settings().chrome_dir)
    cmd = [
        "google-chrome",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,820",
    ]
    if headless:
        cmd.append("--headless=new")
        cmd.append("https://chatgpt.com")
    else:
        cmd.extend(["https://chatgpt.com", "https://claude.ai"])
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"launched": True, "pid": proc.pid, "port": port, "headless": headless, "user_data_dir": user_data_dir}


async def ensure_chrome_cdp(cdp_url: str = "http://127.0.0.1:9223") -> CdpStatusResponse:
    """Auto-activate AIroom's dedicated Chrome CDP if not already listening."""
    status = await inspect_cdp(cdp_url)
    if status.connected:
        return status

    parsed = urlparse(cdp_url)
    port = parsed.port or 9223
    launch_chrome_cdp(port=port)

    for _ in range(30):
        await asyncio.sleep(0.5)
        status = await inspect_cdp(cdp_url)
        if status.connected:
            return status
    return status


async def _ensure_locator_focused(locator) -> None:
    """Verify cursor/focus is inside the target input element, retrying focus if needed."""
    await locator.scroll_into_view_if_needed()
    await locator.click()
    is_focused = await locator.evaluate(
        "(el) => el === document.activeElement || el.contains(document.activeElement)"
    )
    if not is_focused:
        await locator.evaluate("(el) => el.focus()")
        await locator.click()


async def _human_insert_text(page: Page, locator, text: str) -> None:
    """Focus-verified insertion into ProseMirror/textarea without truncation or early Enter."""
    await _ensure_locator_focused(locator)
    await asyncio.sleep(random.uniform(0.2, 0.4))

    if len(text) > 500:
        pasted = await locator.evaluate(
            """(el, txt) => {
                if (el !== document.activeElement && !el.contains(document.activeElement)) {
                    el.focus();
                }
                const dt = new DataTransfer();
                dt.setData('text/plain', txt);
                const ev = new ClipboardEvent('paste', {
                    clipboardData: dt,
                    bubbles: true,
                    cancelable: true
                });
                el.dispatchEvent(ev);
                return (el.innerText || el.value || '').trim().length >= Math.min(txt.length, 150);
            }""",
            text,
        )
        if pasted:
            await asyncio.sleep(random.uniform(0.35, 0.6))
            return

    await _ensure_locator_focused(locator)
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line:
            await page.keyboard.insert_text(line)
        if idx < len(lines) - 1:
            await page.keyboard.press("Shift+Enter")
        await asyncio.sleep(random.uniform(0.015, 0.04))
    await asyncio.sleep(random.uniform(0.3, 0.55))


async def capture_tab_screenshot(cdp_url: str, role: Literal["chatgpt", "claude"]) -> bytes:
    """Capture a live PNG viewport screenshot of the embedded ChatGPT or Claude tab."""
    async with CdpBrowserRelay(cdp_url) as relay:
        page = await relay._find_or_open_tab(role, force_new_tab=False)
        return await page.screenshot(type="png")


async def interact_tab(
    cdp_url: str,
    role: Literal["chatgpt", "claude"],
    action: str,
    x: float | None = None,
    y: float | None = None,
    text: str | None = None,
    key: str | None = None,
    delta_y: float = 0,
    url: str | None = None,
) -> dict:
    """Forward click, typing, scroll, or navigation from the in-app browser pane to CDP."""
    async with CdpBrowserRelay(cdp_url) as relay:
        page = await relay._find_or_open_tab(role, force_new_tab=False)
        if action == "click" and x is not None and y is not None:
            await page.mouse.click(x, y)
        elif action == "type" and text is not None:
            await page.keyboard.insert_text(text)
        elif action == "press" and key:
            await page.keyboard.press(key)
        elif action == "scroll":
            await page.mouse.wheel(0, delta_y)
        elif action == "goto" and url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        elif action == "reload":
            await page.reload(wait_until="domcontentloaded", timeout=30000)
        return {"ok": True, "url": page.url, "title": await page.title()}


async def _check_quota_or_limit(page: Page, provider: str) -> None:
    """Inspect visible alert/toast banners (excluding chat messages/composer) for quota limits."""
    banner_text = await page.evaluate(
        """() => {
            const nodes = document.querySelectorAll(
                "[role='alert'], [data-testid*='limit'], [class*='toast']"
            );
            return Array.from(nodes)
                .filter(n => !n.closest('[data-message-author-role]') && !n.closest('[data-testid="assistant-message"]'))
                .map(n => n.innerText || "")
                .join("\\n")
                .toLowerCase();
        }"""
    )
    for pat in _QUOTA_PATTERNS:
        if pat in banner_text:
            raise QuotaExhaustedError(provider, pat)


def _has_unclosed_marker(text: str) -> bool:
    """Return True if a [PLAN_ITEM] or [REVIEW_ITEM] block started without its closing tag."""
    open_plan = text.count("[PLAN_ITEM")
    close_plan = text.count("[/PLAN_ITEM]")
    open_rev = text.count("[REVIEW_ITEM")
    close_rev = text.count("[/REVIEW_ITEM]")
    return open_plan > close_plan or open_rev > close_rev


async def _wait_for_response_complete(
    page: Page,
    provider: str,
    msg_locator,
    prev_count: int,
    stop_selector: str,
    continue_selector: str,
    prev_last_text: str = "",
    timeout_s: int = 180,
) -> str:
    """Wait until streaming finishes, auto-clicking 'Continue generating' if needed."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_text = ""
    stable_ticks = 0
    unclosed_grace_ticks = 0

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.2)
        await _check_quota_or_limit(page, provider)

        # Free-tier models sometimes pause mid-output with a Continue button
        cont_btn = page.locator(continue_selector).first
        if await cont_btn.count() > 0 and await cont_btn.is_visible():
            await cont_btn.click()
            stable_ticks = 0
            continue

        cur_count = await msg_locator.count()
        if cur_count == 0:
            continue

        latest = msg_locator.nth(cur_count - 1)
        cur_text = (await latest.inner_text()).strip()
        # Handle both appended DOM nodes and virtualized DOMs where count stays 1
        if cur_count <= prev_count and cur_text == prev_last_text:
            continue

        stop_btn = page.locator(stop_selector)
        is_streaming = await stop_btn.count() > 0 and await stop_btn.first.is_visible()

        if not is_streaming and cur_text and cur_text == last_text:
            if _has_unclosed_marker(cur_text) and unclosed_grace_ticks < 6:
                unclosed_grace_ticks += 1
                continue
            stable_ticks += 1
            if stable_ticks >= 3:
                return cur_text
        else:
            stable_ticks = 0
            unclosed_grace_ticks = 0
            last_text = cur_text

    if last_text:
        return last_text
    raise TimeoutError(f"Timed out waiting for {provider} response over CDP.")


class CdpBrowserRelay:
    """Drives ChatGPT and Claude tabs over Chrome DevTools Protocol (CDP)."""

    def __init__(self, cdp_url: str) -> None:
        self.cdp_url = cdp_url.rstrip("/")
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._pages: dict[str, Page] = {}

    async def __aenter__(self) -> "CdpBrowserRelay":
        self._pw = await async_playwright().start()
        endpoint = resolve_cdp_endpoint(self.cdp_url)
        self._browser = await self._pw.chromium.connect_over_cdp(endpoint)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def _find_or_open_tab(self, role: Literal["chatgpt", "claude"], force_new_tab: bool = False) -> Page:
        assert self._browser is not None
        if not force_new_tab:
            cached = self._pages.get(role)
            if cached and not cached.is_closed():
                await cached.bring_to_front()
                return cached
            for context in self._browser.contexts:
                for page in context.pages:
                    if _classify_url(page.url) == role:
                        await page.bring_to_front()
                        self._pages[role] = page
                        return page

        # Open a new tab in the first context (keeps user's existing tabs untouched)
        context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        page = await context.new_page()
        target_url = "https://chatgpt.com/" if role == "chatgpt" else "https://claude.ai/new"
        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2.0)
        self._pages[role] = page
        return page

    async def send_to_chatgpt(self, prompt: str, new_chat: bool = False, timeout_s: int = 180) -> str:
        page = await self._find_or_open_tab("chatgpt", force_new_tab=new_chat)

        await _check_quota_or_limit(page, "ChatGPT")

        # Locate ChatGPT's actual composer (never match CodeMirror .cm-content in chat history)
        input_locator = page.locator(
            "#prompt-textarea:visible, div.ProseMirror[contenteditable='true']:visible"
        ).last
        await input_locator.wait_for(state="visible", timeout=20000)

        # Count existing assistant messages before sending
        msg_locator = page.locator(
            "[data-message-author-role='assistant'], [data-content-search-unit-key$=':assistant']"
        )
        prev_count = await msg_locator.count()
        prev_last_text = (await msg_locator.nth(prev_count - 1).inner_text()).strip() if prev_count > 0 else ""

        await _human_insert_text(page, input_locator, prompt)
        send_btn = page.locator(
            "button[data-testid='send-button'], button[aria-label='إرسال'], button[aria-label='Send prompt']"
        ).first
        if await send_btn.count() > 0 and await send_btn.is_enabled():
            await send_btn.click()
        else:
            await page.keyboard.press("Enter")

        return await _wait_for_response_complete(
            page=page,
            provider="ChatGPT",
            msg_locator=msg_locator,
            prev_count=prev_count,
            stop_selector="button[data-testid='stop-button'], button[aria-label*='Stop'], button[aria-label*='إيقاف']",
            continue_selector="button:has-text('Continue generating'), button:has-text('Continue'), button:has-text('متابعة التوليد'), button:has-text('متابعة'), button:has-text('أكمل')",
            prev_last_text=prev_last_text,
            timeout_s=timeout_s,
        )

    async def send_to_claude(self, prompt: str, new_chat: bool = False, timeout_s: int = 180) -> str:
        page = await self._find_or_open_tab("claude", force_new_tab=new_chat)

        await _check_quota_or_limit(page, "Claude")

        # Claude uses ProseMirror contenteditable composer
        input_locator = page.locator(
            "div.ProseMirror[contenteditable='true']:visible, [data-testid='chat-input'] [contenteditable='true']:visible"
        ).last
        await input_locator.wait_for(state="visible", timeout=20000)

        # Track existing assistant message containers
        msg_locator = page.locator(
            "[data-testid='assistant-message'], [data-testid='chat-message-text'], .font-claude-message, .font-claude-response"
        )
        prev_count = await msg_locator.count()
        prev_last_text = (await msg_locator.nth(prev_count - 1).inner_text()).strip() if prev_count > 0 else ""

        await _human_insert_text(page, input_locator, prompt)
        send_btn = page.locator(
            "button[data-testid='chat-input-send'], button[aria-label='Send message'], button[aria-label='إرسال']"
        ).first
        if await send_btn.count() > 0 and await send_btn.is_enabled():
            await send_btn.click()
        else:
            await page.keyboard.press("Enter")

        return await _wait_for_response_complete(
            page=page,
            provider="Claude",
            msg_locator=msg_locator,
            prev_count=prev_count,
            stop_selector="[data-is-streaming='true'], button[aria-label*='Stop'], button[aria-label*='stop'], button[aria-label*='إيقاف']",
            continue_selector="button:has-text('Continue'), button:has-text('متابعة')",
            prev_last_text=prev_last_text,
            timeout_s=timeout_s,
        )
