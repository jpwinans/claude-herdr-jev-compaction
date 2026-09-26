#!/usr/bin/env python3
"""Live Jev evaluation only: never imports or invokes compaction hooks."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent

def credential():
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key and sys.platform == "darwin":
        result = subprocess.run(["security", "find-generic-password", "-a", os.environ.get("USER", ""),
                                 "-s", "typesafe-api-key", "-w"], capture_output=True, text=True, timeout=5)
        key = result.stdout.strip()
    if not key:
        raise RuntimeError("No Jev credential available")
    return key

def judge(case, questions, key):
    state = {k: case[k][-limit:] for k, limit in (("user_request", 4000), ("assistant_final_reply", 8000))}
    body = dict(model="jev-latest", state=state, questions=questions)
    request = urllib.request.Request("https://api.typesafe.ai/v1/systemone", json.dumps(body).encode(),
                                     {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    start = time.monotonic()
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = json.load(response)
            answers = raw["answers"]
            done, waiting = (answers[k]["noul"] for k in ("done", "waiting"))
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0 <= x <= 1 for x in (done, waiting)):
                raise ValueError("Invalid score")
            fire = done >= .75 and waiting < .3
            return dict(id=case["id"], category=case["category"], expected=case["expected"], done=done,
                        waiting=waiting, fire=fire, correct=fire == case["expected"], attempts=attempt,
                        elapsed_seconds=round(time.monotonic()-start, 3), response=raw)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 529) or attempt == 3:
                return dict(id=case["id"], expected=case["expected"], error=f"HTTP {error.code}")
            time.sleep(2 ** attempt)
        except Exception as error:
            return dict(id=case["id"], expected=case["expected"], error=type(error).__name__)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("selection", help="batch1..batch5, negatives, all, positives, or comma-separated IDs")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    question_bytes = (ROOT / f"{args.version}.json").read_bytes()
    cases_bytes = (ROOT / "cases.json").read_bytes()
    cases = json.loads(cases_bytes)
    if args.selection.startswith("batch"):
        cases = [c for c in cases if c["batch"] == int(args.selection[5:])]
    elif args.selection in ("negatives", "positives"):
        cases = [c for c in cases if c["expected"] == (args.selection == "positives")]
    elif args.selection != "all":
        ids = args.selection.split(",")
        cases = [c for c in cases if c["id"] in ids]
        assert len(cases) == len(ids), "Unknown or duplicate ID"
    assert cases
    key = credential()
    results = []
    destination = ROOT / "results" / f"{args.version}-{args.selection}{args.tag}.json"
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():
        raise RuntimeError("Refusing to overwrite an evaluation; use --tag")
    metadata = dict(version=args.version, selection=args.selection, model="jev-latest",
                    timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    questions_sha256=hashlib.sha256(question_bytes).hexdigest(),
                    cases_sha256=hashlib.sha256(cases_bytes).hexdigest(), thresholds=dict(done_min=.75, waiting_max=.3))
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(judge, c, json.loads(question_bytes), key) for c in cases]
        for job in as_completed(jobs):
            result = job.result()
            results.append(result)
            destination.write_text(json.dumps(dict(metadata=metadata, results=sorted(results, key=lambda r:r["id"])), indent=2) + "\n")
            print(json.dumps({k:v for k,v in result.items() if k != "response"}), flush=True)
    print(json.dumps(dict(total=len(results), correct=sum(r.get("correct", False) for r in results),
                         errors=sum("error" in r for r in results), output=str(destination))), flush=True)

if __name__ == "__main__":
    main()
