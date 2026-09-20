"""Drive the operations console in a real browser and report layout breakage.

Not part of CI: it needs a browser, and CI has no console to point it at. It
exists so "the mobile layout works" can be re-checked rather than believed —
the responsive rules were written once against a desktop window, and the
problems it found (fields truncated to 150px, a card title wrapping over three
lines, a 32px nav toggle) were all invisible at that width.

    pip install playwright && playwright install chromium
    python tools/responsive_audit.py --base http://127.0.0.1:8000/ \
        --email admin@swarajsolar.com --password ...

Screenshots land in --out. It exits non-zero if any page scrolls sideways.
"""

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

SIZES = [("360x800", 360, 800), ("390x844", 390, 844),
         ("768x1024", 768, 1024), ("1440x900", 1440, 900)]
PAGES = ["dashboard", "leads", "campaigns", "myleads", "surveys", "calculator"]

# An element reaching past the viewport means a sideways scroll, unless it sits
# in a box that scrolls on purpose (a wide table), where it is still reachable.
OVERFLOW = """
() => {
  const vw = document.documentElement.clientWidth;
  const out = { vw, docScroll: document.documentElement.scrollWidth, wide: [] };
  for (const node of document.querySelectorAll('body *')) {
    const r = node.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    let inScroller = false;
    for (let a = node.parentElement; a; a = a.parentElement) {
      const ox = getComputedStyle(a).overflowX;
      if (ox === 'auto' || ox === 'scroll') { inScroller = true; break; }
    }
    const own = getComputedStyle(node).overflowX;
    const past = Math.round(r.right - vw);
    if (past > 1 && !inScroller && own !== 'auto' && own !== 'scroll') {
      out.wide.push({ tag: node.tagName.toLowerCase(),
                      cls: (node.className || '').toString().slice(0, 60), past });
    }
  }
  out.wide = out.wide.slice(0, 12);
  return out;
}
"""

# Controls below roughly 40px square are hard to hit accurately with a thumb.
SMALL_TAPS = """
() => {
  const small = [];
  for (const n of document.querySelectorAll('button, a, input[type=submit], select')) {
    const r = n.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.height < 36 || r.width < 36) {
      small.push({ txt: (n.textContent || '').trim().slice(0, 24),
                   cls: (n.className || '').toString().slice(0, 40),
                   w: Math.round(r.width), h: Math.round(r.height) });
    }
  }
  return small.slice(0, 10);
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", default="/tmp/console-shots")
    ap.add_argument("--browser", default=None,
                    help="Chromium executable, if not the one Playwright downloaded")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failures = []

    with sync_playwright() as pw:
        launch = {"executable_path": args.browser} if args.browser else {}
        browser = pw.chromium.launch(**launch)

        for label, width, height in SIZES:
            phone = width < 500
            ctx = browser.new_context(viewport={"width": width, "height": height},
                                      device_scale_factor=2 if phone else 1,
                                      is_mobile=phone, has_touch=phone)
            page = ctx.new_page()
            page.goto(args.base, wait_until="networkidle")
            page.screenshot(path=str(out / f"{label}-login.png"), full_page=True)

            page.fill("#email", args.email)
            page.fill("#password", args.password)
            page.click("#login-submit")
            page.wait_for_selector("#app:not([hidden])", timeout=15000)
            page.wait_for_timeout(700)

            for name in PAGES:
                page.evaluate(f"render('{name}')")
                page.wait_for_timeout(900)
                page.screenshot(path=str(out / f"{label}-{name}.png"), full_page=True)
                result = page.evaluate(OVERFLOW)
                if result["docScroll"] > result["vw"] + 1 or result["wide"]:
                    failures.append(f"{label} {name}: scrollWidth {result['docScroll']} "
                                    f"vs viewport {result['vw']}")
                    for item in result["wide"]:
                        failures.append(f"    {item['tag']}.{item['cls']} past={item['past']}px")

            if width <= 900:
                page.evaluate("render('dashboard')")
                page.click("#nav-toggle")
                page.wait_for_timeout(450)
                page.screenshot(path=str(out / f"{label}-nav-open.png"))
                for tap in page.evaluate(SMALL_TAPS):
                    print(f"[{label}] small tap target: {tap['txt']!r} "
                          f"{tap['w']}x{tap['h']} ({tap['cls']})")
            ctx.close()
        browser.close()

    print(f"\nscreenshots: {out}")
    if failures:
        print("\nHORIZONTAL OVERFLOW:")
        for line in failures:
            print(line)
        return 1
    print("no horizontal overflow at any tested width")
    return 0


if __name__ == "__main__":
    sys.exit(main())
