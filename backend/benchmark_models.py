#!/usr/bin/env python3
"""
benchmark_models.py

Simple benchmarking tool to compare response latency and success for multiple chat models
and optionally multiple providers (official OpenAI endpoint and OpenRouter). It also
includes a small wrapper to demonstrate fallback behavior.

Usage examples:
  # run default models (local OpenAI key required)
  OPENAI_API_KEY="sk-..." python3 benchmark_models.py --trials 3

  # test a custom list of models and OpenRouter base URL
  OPENAI_API_KEY="sk-..." OPENROUTER_BASE_URL="https://openrouter.ai/api/v1" \
    python3 benchmark_models.py --models "gpt-3.5-turbo,gpt-4o-mini" --trials 5

The script writes a JSON report `benchmark_report.json` in the current directory.
"""
import os
import time
import json
import argparse
from typing import List, Dict, Any
from openai import OpenAI, OpenAIError, RateLimitError

DEFAULT_MODELS = [
    # official models
    "deepseek/deepseek-r1-0528-qwen3-8b:free", #ok
    "deepseek/deepseek-r1-0528:free", #rate-limit
    "deepseek/deepseek-chat-v3-0324:free", #rate-limit
    "deepseek/deepseek-r1-distill-llama-70b:free", #ok
    "deepseek/deepseek-r1:free", #rate-limit
]

SAMPLE_MESSAGES = [
    {"role": "user", "content": "Summarize the main idea of the following text: 'The quick brown fox jumps over the lazy dog' in one sentence."}
]


def make_client(base_url: str | None, api_key: str) -> OpenAI:
    kws = {"api_key": api_key}
    if base_url:
        kws["base_url"] = base_url
    return OpenAI(**kws)


def run_single_request(client: OpenAI, model: str, timeout: int = 30, retries: int = 3) -> Dict[str, Any]:
    """
    Run a single chat completion with retry/backoff for rate limits.
    """
    attempt = 0
    base_backoff = 0.5
    while True:
        start = time.perf_counter()
        try:
            resp = client.chat.completions.create(model=model, messages=SAMPLE_MESSAGES, max_tokens=200)
            elapsed = time.perf_counter() - start
            usage = getattr(resp, 'usage', None) or (resp.get('usage') if isinstance(resp, dict) else None)
            text = None
            try:
                text = resp.choices[0].message.content
            except Exception:
                try:
                    text = resp.get('choices', [{}])[0].get('message', {}).get('content')
                except Exception:
                    text = None
            return {"ok": True, "elapsed": elapsed, "usage": usage, "text_sample": (text[:200] if text else None)}
        except RateLimitError as e:
            elapsed = time.perf_counter() - start
            attempt += 1
            if attempt > retries:
                return {"ok": False, "error": f"rate_limited: {e}", "elapsed": elapsed}
            sleep_for = base_backoff * (2 ** (attempt - 1))
            # jitter
            jitter = (os.urandom(1)[0] / 255.0) * 0.5 * sleep_for
            time.sleep(sleep_for + jitter)
            continue
        except OpenAIError as e:
            elapsed = time.perf_counter() - start
            return {"ok": False, "error": str(e), "elapsed": elapsed}
        except Exception as e:
            elapsed = time.perf_counter() - start
            return {"ok": False, "error": str(e), "elapsed": elapsed}


def benchmark_providers(models: List[str], trials: int, api_key: str, openrouter_base: str | None):
    report = {"run_ts": time.time(), "env": {"openrouter_base": openrouter_base is not None}, "results": []}

    providers = []
    # official OpenAI (no base_url)
    providers.append({"name": "openai", "base_url": None})
    if openrouter_base:
        providers.append({"name": "openrouter", "base_url": openrouter_base})

    for prov in providers:
        client = make_client(prov["base_url"], api_key)
        for model in models:
            stats = {"provider": prov["name"], "model": model, "trials": []}
            for i in range(trials):
                r = run_single_request(client, model)
                stats["trials"].append(r)
                # small delay to avoid being throttled
                time.sleep(0.5)
            # summarize
            successes = [t for t in stats["trials"] if t.get("ok")]
            failures = [t for t in stats["trials"] if not t.get("ok")]
            latencies = [t.get("elapsed") for t in successes]
            stats["summary"] = {
                "success_count": len(successes),
                "failure_count": len(failures),
                "avg_latency": (sum(latencies)/len(latencies)) if latencies else None,
                "min_latency": (min(latencies) if latencies else None),
                "max_latency": (max(latencies) if latencies else None),
            }
            report["results"].append(stats)
    return report


def fallback_completion(models: List[str], api_key: str, openrouter_base: str | None):
    """
    Example fallback: try OpenRouter first (if configured) for a model, then official OpenAI.
    Returns first successful response and which provider served it.
    """
    providers = []
    if openrouter_base:
        providers.append({"name": "openrouter", "base_url": openrouter_base})
    providers.append({"name": "openai", "base_url": None})

    for model in models:
        for prov in providers:
            client = make_client(prov["base_url"], api_key)
            print(f"Trying model={model} provider={prov['name']}")
            r = run_single_request(client, model)
            if r.get("ok"):
                print(f"Success with {prov['name']} ({model}) in {r['elapsed']:.2f}s")
                return {"provider": prov['name'], "model": model, "result": r}
            else:
                print(f"Failed {prov['name']} ({model}): {r.get('error')}")
    return None


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--models', help='Comma-separated model names to test', default=','.join(DEFAULT_MODELS))
    p.add_argument('--trials', type=int, default=3)
    p.add_argument('--out', default='benchmark_report.json')
    args = p.parse_args()

    # api_key = os.environ.get('OPENAI_API_KEY')
    api_key = "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"
    if not api_key:
        print('Set OPENAI_API_KEY environment variable')
        raise SystemExit(1)
    # openrouter_base = os.environ.get('OPENROUTER_BASE_URL')
    openrouter_base = "https://openrouter.ai/api/v1"

    models = [m.strip() for m in args.models.split(',') if m.strip()]
    print(f"Benchmarking models: {models} trials={args.trials} providers: openrouter={'yes' if openrouter_base else 'no'}")

    report = benchmark_providers(models, args.trials, api_key, openrouter_base)
    with open(args.out, 'w') as fh:
        json.dump(report, fh, indent=2)
    print('Wrote', args.out)

    # Example fallback demo
    print('\nFallback demo: try models sequentially and providers in configured order')
    fb = fallback_completion(models, api_key, openrouter_base)
    if fb:
        print('Fallback returned:', fb['provider'], fb['model'])
    else:
        print('Fallback: no provider/model combination succeeded')
