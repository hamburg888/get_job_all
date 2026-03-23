from app.core.config import settings


STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});

Object.defineProperty(navigator, 'languages', {
  get: () => ['zh-CN', 'zh', 'en'],
});

window.chrome = {
  runtime: {},
  loadTimes: function () {},
  csi: function () {},
  app: {},
};

const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters && parameters.name === 'notifications'
    ? Promise.resolve({ state: 'denied' })
    : originalQuery(parameters)
);

const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
  if (parameter === 37445) return 'Intel Inc.';
  if (parameter === 37446) return 'Intel(R) Iris(R) Xe Graphics';
  return originalGetParameter.call(this, parameter);
};
"""


def build_browser_launch_options(headless: bool) -> dict:
    effective_headless = bool(headless and not settings.browser_fake_headless)
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--disable-features=CalculateNativeWinOcclusion",
        "--disable-features=VizDisplayCompositor",
        "--disable-notifications",
        "--disable-blink-features=AutomationControlled",
        "--lang=zh-CN,zh;q=0.9,en;q=0.8",
    ]
    if headless and settings.browser_fake_headless:
        args.extend(
            [
                "--window-position=-10000,-10000",
                "--window-size=1920,1080",
                "--start-maximized",
                "--disable-webgl",
            ]
        )
    elif effective_headless:
        args.extend(
            [
                "--window-size=1920,1080",
            ]
        )
    else:
        args.extend(
            [
                "--start-maximized",
            ]
        )
    return {
        "headless": effective_headless,
        "args": args,
        "locale": "zh-CN",
        "viewport": {"width": 1920, "height": 1080},
        "user_agent": settings.browser_user_agent,
    }
