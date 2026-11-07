# FormFiller — Blast Radius Zero

An agent that fills multi-step web forms inside a **throwaway Docker container**,
screenshots every step, verifies each field against your profile, and then
**stops before submit**. Nothing is sent until a human looks at the final
screenshots and approves. When the run ends, the container and its browser
profile are destroyed — cookies, sessions and credentials die with it.

Built for the **Vultr Agent Rush** hackathon, Track 1: Blast Radius Zero.

## Why

Agents that fill forms (job applications, sign-ups, onboarding) act on the real
web with real consequences: one wrong field and a real application is submitted
with wrong data, or a real account is created on a real site. FormFiller's
answer is containment, not trust:

1. **Disposable sandbox** — every run is a fresh container with a fresh browser
   profile. The blast radius of a bad run is one container.
2. **Screenshot-verified steps** — the agent photographs the form before and
   after every step, and checks filled values against the source profile.
3. **Human approval gate** — the agent never clicks the final submit. A person
   reviews the screenshots and either drops an `APPROVE` file or lets the run
   expire. Unapproved runs submit nothing.

## How it works

```
profile.json ──► filler.py ──► Playwright (Chromium, in container)
                     │            fill step → screenshot → verify → next step
                     │
                     ├─► runs/<id>/step-*.png      (evidence)
                     ├─► runs/<id>/approval.json   (what will be submitted)
                     └─► waits for runs/<id>/APPROVE  ──► submit ──► result.png
```

- Field mapping is heuristic (label/name/placeholder matching) with an optional
  LLM check: set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` to any
  OpenAI-compatible endpoint (the demo uses Vultr Serverless Inference) and the
  agent asks the model to flag mismatches between the profile and the filled
  form before it asks the human.
- Multi-page forms are handled page by page; answers so far are written to the
  run dir after each page, so a crashed run shows exactly how far it got.

## Run the demo

```bash
docker compose up --build demo        # the 3-step demo application form
docker compose run --rm agent python filler.py \
  --url http://demo:8080 --data profile.example.json --hold-seconds 300
# inspect runs/<id>/ screenshots, then:  touch runs/<id>/APPROVE
```

Or against any real form URL, on the VM:

```bash
python filler.py --url https://example.com/apply --data profile.json
```

Exit codes: `0` submitted after approval, `2` finished filling, awaiting
approval (or approval timed out — nothing submitted), `1` error.

## Layout

- `agent/filler.py` — the agent (Playwright, sync API)
- `agent/Dockerfile` — Playwright Python image, nothing persists
- `demo-form/index.html` — a deliberately awkward 3-step application form
  (generic labels, a review page, no state saving) used for the demo video
- `docker-compose.yml` — demo form + agent

## Demo video outline

1. The problem: an agent, a real form, one wrong field = a real wrong
   submission. (30s)
2. FormFiller runs in its container: step screenshots, field check flags a
   deliberate mismatch, agent fixes it. (60s)
3. **The containment moment**: the agent reaches Submit and stops. The human
   rejects the run. Nothing was sent; the container dies. (30s)
4. Second run, human approves, exactly one submission lands. (30s)
