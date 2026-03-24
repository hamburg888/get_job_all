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

if (window.navigator.userAgentData) {
  Object.defineProperty(window.navigator, 'userAgentData', {
    get: () => ({
      brands: [
        { brand: 'Google Chrome', version: '134' },
        { brand: 'Chromium', version: '134' },
      ],
      mobile: false,
      platform: 'Windows',
      getHighEntropyValues: async () => ({
        architecture: 'x86',
        bitness: '64',
        brands: [
          { brand: 'Google Chrome', version: '134' },
          { brand: 'Chromium', version: '134' },
        ],
        mobile: false,
        model: '',
        platform: 'Windows',
        platformVersion: '10.0.0',
        uaFullVersion: '134.0.0.0',
      }),
      toJSON: () => ({
        brands: [
          { brand: 'Google Chrome', version: '134' },
          { brand: 'Chromium', version: '134' },
        ],
        mobile: false,
        platform: 'Windows',
      }),
    }),
  });
}

Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth + 12 });
Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 96 });
"""


LAODENG_INIT_SCRIPT = """
(() => {
  'use strict';
  const nativeFunctionToString = Function.prototype.toString;
  const nativeSourceMap = new WeakMap();

  const registerNativeSource = (fn, source) => {
    try {
      nativeSourceMap.set(fn, source);
    } catch (err) {
      void err;
    }
  };

  Object.defineProperty(Function.prototype, 'toString', {
    configurable: true,
    writable: true,
    value: function toString() {
      if (nativeSourceMap.has(this)) {
        return nativeSourceMap.get(this);
      }
      return nativeFunctionToString.call(this);
    },
  });

  registerNativeSource(Function.prototype.toString, nativeFunctionToString.toString());

  const stealthify = (obj, prop, handler) => {
    const original = obj[prop];
    if (typeof original !== 'function') {
      return;
    }

    const wrapped = function (...args) {
      return handler.call(this, original, args);
    };

    try {
      Object.defineProperty(wrapped, 'name', {
        ...Object.getOwnPropertyDescriptor(wrapped, 'name'),
        value: prop,
      });
    } catch (err) {
      void err;
    }

    try {
      Object.setPrototypeOf(wrapped, Object.getPrototypeOf(original));
    } catch (err) {
      void err;
    }

    registerNativeSource(wrapped, nativeFunctionToString.call(original));

    const descriptor = Object.getOwnPropertyDescriptor(obj, prop);
    Object.defineProperty(obj, prop, {
      ...descriptor,
      value: wrapped,
    });
  };

  const filterConsoleArgs = (args) =>
    args.map((arg) => {
      if (arg && typeof arg === 'object') {
        return {};
      }
      return arg;
    });

  ['log', 'debug', 'info', 'warn', 'error', 'dir', 'table'].forEach((name) => {
    stealthify(console, name, (original, args) => original.apply(console, filterConsoleArgs(args)));
  });

  registerNativeSource(
    registerNativeSource,
    'function registerNativeSource() { [native code] }'
  );
})();
"""


def build_browser_launch_options(headless: bool) -> dict:
    effective_headless = bool(headless and not settings.browser_fake_headless)
    args = [
        "--disable-notifications",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--lang=zh-CN,zh;q=0.9,en;q=0.8",
    ]
    if headless and settings.browser_fake_headless:
        args.extend(
            [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-position=-10000,-10000",
                "--window-size=1920,1080",
                "--start-maximized",
                "--disable-webgl",
            ]
        )
    elif effective_headless:
        args.extend(
            [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
            ]
        )
    else:
        args.extend(
            [
                "--window-size=1440,760",
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
            ]
        )
    options = {
        "headless": effective_headless,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
        "channel": "chrome",
        "locale": "zh-CN",
        "ignore_https_errors": True,
        "user_agent": settings.browser_user_agent,
    }
    options["viewport"] = {"width": 1440, "height": 760} if not effective_headless else {"width": 1920, "height": 1080}
    return options


def build_visible_login_launch_options() -> dict:
    args = [
        "--disable-notifications",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--lang=zh-CN,zh;q=0.9,en;q=0.8",
        "--window-size=1440,760",
        "--start-maximized",
    ]
    return {
        "headless": False,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
        "channel": "chrome",
        "locale": "zh-CN",
        "viewport": {"width": 1440, "height": 760},
        "ignore_https_errors": True,
        "user_agent": settings.browser_user_agent,
    }
