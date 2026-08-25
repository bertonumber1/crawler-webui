#!/usr/bin/env python3
import argparse,json
from crawler.jd_rules import examples,validate,save
ap=argparse.ArgumentParser()
ap.add_argument("--output",default="jd_rules/example.linkcrawlerrules.json")
ap.add_argument("--validate")
a=ap.parse_args()
if a.validate:
    r=validate(json.load(open(a.validate,encoding="utf-8"))); print(json.dumps(r,indent=2)); raise SystemExit(0 if r["ok"] else 1)
r=examples(); print(json.dumps(validate(r),indent=2)); print(save(r,a.output))
