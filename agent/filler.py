#!/usr/bin/env python3
"""FormFiller agent: fills a multi-step web form inside a disposable
container, screenshots every step, and never submits without a human
dropping an APPROVE file into the run directory.

Usage:
  python filler.py --url <form-url> --data profile.json [--hold-seconds 300]

Exit codes: 0 submitted after approval, 2 filled but not approved
(nothing submitted), 1 error.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SUBMIT_WORDS = ("submit", "apply", "send", "confirm", "finish", "place order")
NEXT_WORDS = ("next", "continue", "proceed", "save and continue")


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def field_score(field, key):
    """Heuristic match between a form field and a profile key."""
    hay = norm(" ".join([field.get("label", ""), field.get("name", ""),
                         field.get("placeholder", ""), field.get("id", "")]))
    want = norm(key.replace("_", " "))
    if not hay or not want:
        return 0
    if want in hay or hay in want:
        return 3
    words = set(want.split()) & set(hay.split())
    return len(words)


def extract_fields(page):
    return page.evaluate(
        """() => {
          const out = [];
          document.querySelectorAll('input, textarea, select').forEach((el, i) => {
            if (['hidden','submit','button','file'].includes(el.type)) return;
            const lab = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
            out.push({
              idx: i, tag: el.tagName.toLowerCase(), type: el.type || '',
              name: el.name || '', id: el.id || '',
              placeholder: el.placeholder || '',
              label: lab ? lab.textContent.trim() : (el.getAttribute('aria-label') || ''),
              value: el.value || ''
            });
          });
          return out;
        }"""
    )


def llm_check(profile, filled):
    """Optional OpenAI-compatible check: ask the model to flag mismatches.
    Returns a list of warning strings. Skipped silently when unconfigured."""
    base = os.environ.get("LLM_BASE_URL")
    key = os.environ.get("LLM_API_KEY")
    model = os.environ.get("LLM_MODEL")
    if not (base and key and model):
        return []
    import urllib.request

    prompt = (
        "You check a filled web form against the source profile. "
        "Profile JSON: " + json.dumps(profile) + "\n"
        "Filled fields (label -> value): " + json.dumps(filled) + "\n"
        "Reply with a JSON array of short strings, one per filled field whose "
        "value does not match the profile or looks wrong for its label. "
        "Reply [] when everything matches."
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                 data=body, method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.loads(res.read())
        text = data["choices"][0]["message"]["content"]
        match = re.search(r"\[.*\]", text, re.S)
        return json.loads(match.group(0)) if match else [text.strip()]
    except Exception as exc:  # the check is advisory, never fatal
        return [f"llm check unavailable: {exc}"]


def click_by_words(page, words):
    for word in words:
        btn = page.get_by_role("button", name=re.compile(word, re.I))
        if btn.count() and btn.first.is_visible():
            btn.first.click()
            return word
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--data", required=True, help="profile JSON file")
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--hold-seconds", type=int, default=300,
                    help="how long to wait for the APPROVE file")
    args = ap.parse_args()

    profile = json.loads(Path(args.data).read_text())
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.runs) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    filled_all = {}
    step = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(args.url, wait_until="networkidle")

        while True:
            step += 1
            page.screenshot(path=str(run_dir / f"step-{step:02d}-before.png"))
            fields = extract_fields(page)
            filled = {}
            for f in fields:
                if f["value"]:
                    continue
                best_key, best_score = None, 0
                for key in profile:
                    s = field_score(f, key)
                    if s > best_score:
                        best_key, best_score = key, s
                if best_key and best_score >= 2:
                    value = str(profile[best_key])
                    sel = f"#{f['id']}" if f["id"] else (
                        f"[name='{f['name']}']" if f["name"] else None)
                    if sel:
                        if f["tag"] == "select":
                            page.select_option(sel, value)
                        else:
                            page.fill(sel, value)
                        filled[f["label"] or f["name"] or f["id"]] = value
            filled_all[f"step-{step}"] = filled
            page.screenshot(path=str(run_dir / f"step-{step:02d}-after.png"))
            (run_dir / "progress.json").write_text(json.dumps(filled_all, indent=2))

            if click_by_words(page, NEXT_WORDS) and not _has_submit(page):
                page.wait_for_load_state("networkidle")
                continue
            break

        warnings = llm_check(profile, filled_all)
        approval = {
            "url": args.url, "run": run_id, "filled": filled_all,
            "warnings": warnings, "submitted": False,
        }
        (run_dir / "approval.json").write_text(json.dumps(approval, indent=2))
        print(json.dumps({"run": run_id, "state": "awaiting_approval",
                          "warnings": warnings}))

        # The gate: only an APPROVE file from a human lets us submit.
        deadline = time.time() + args.hold_seconds
        approved = False
        while time.time() < deadline:
            if (run_dir / "APPROVE").exists():
                approved = True
                break
            time.sleep(2)
        if not approved:
            print(json.dumps({"run": run_id, "state": "not_approved",
                              "submitted": False}))
            browser.close()
            return 2

        clicked = click_by_words(page, SUBMIT_WORDS)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(run_dir / "result.png"))
        approval["submitted"] = bool(clicked)
        (run_dir / "approval.json").write_text(json.dumps(approval, indent=2))
        print(json.dumps({"run": run_id, "state": "submitted",
                          "submitted": bool(clicked)}))
        browser.close()
        return 0 if clicked else 1


def _has_submit(page):
    for word in SUBMIT_WORDS:
        btn = page.get_by_role("button", name=re.compile(word, re.I))
        if btn.count() and btn.first.is_visible():
            return True
    return False


if __name__ == "__main__":
    sys.exit(main())
