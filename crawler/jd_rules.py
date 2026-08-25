"""JDownloader 2 LinkCrawler rule builder/validator.

Rule names follow JDownloader's vocabulary:
DEEPDECRYPT, REWRITE, DIRECTHTTP, FOLLOWREDIRECT, SUBMITFORM.
"""
from pathlib import Path
import json, os, re
try:
    import jsonschema
except ImportError:
    jsonschema=None
RULE_TYPES={"DEEPDECRYPT","REWRITE","DIRECTHTTP","FOLLOWREDIRECT","SUBMITFORM"}
SCHEMA_DIR=Path(__file__).resolve().parent.parent/"jd_rules"

def build_rule(name, pattern, rule, enabled=True, logging=False, maxDecryptDepth=1,
               cookies=None, updateCookies=None, packageNamePattern=None,
               passwordPattern=None, formPattern=None, deepPattern=None,
               rewriteReplaceWith=None):
    rule=rule.upper()
    if rule not in RULE_TYPES: raise ValueError(f"unsupported rule type: {rule}")
    re.compile(pattern)
    if maxDecryptDepth < 0: raise ValueError("maxDecryptDepth must be >= 0")
    return {
      "enabled":bool(enabled),"logging":bool(logging),"maxDecryptDepth":int(maxDecryptDepth),
      "name":name,"pattern":pattern,"rule":rule,
      "packageNamePattern":packageNamePattern if rule=="DEEPDECRYPT" else None,
      "passwordPattern":passwordPattern if rule=="DEEPDECRYPT" else None,
      "formPattern":formPattern if rule=="SUBMITFORM" else None,
      "deepPattern":deepPattern if rule=="DEEPDECRYPT" else None,
      "rewriteReplaceWith":rewriteReplaceWith if rule=="REWRITE" else None,
      "cookies":cookies if rule in {"DIRECTHTTP","DEEPDECRYPT","SUBMITFORM","FOLLOWREDIRECT"} else None,
      "updateCookies":updateCookies if rule in {"DIRECTHTTP","DEEPDECRYPT","SUBMITFORM","FOLLOWREDIRECT"} else None,
    }

def validate(rules):
    if not isinstance(rules,list): rules=[rules]
    if jsonschema is None: return {"ok":True,"warning":"jsonschema unavailable; structural checks only"}
    schema=json.loads((SCHEMA_DIR/"jd2mcr.schema.json").read_text())
    errors=sorted(jsonschema.Draft7Validator(schema).iter_errors(rules),key=lambda e:list(e.path))
    return {"ok":not errors,"errors":[e.message for e in errors]}

def save(rules,path):
    v=validate(rules)
    if not v["ok"]: raise ValueError("; ".join(v["errors"]))
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(str(p)+".tmp"); tmp.write_text(json.dumps(rules,indent=2)+"\n",encoding="utf-8"); os.replace(tmp,p)
    return str(p)

def examples():
    return [
      build_rule("Example deep crawl",r"https?://(?:www\.)?example\.org/releases/[^?#]+","DEEPDECRYPT",
                 logging=True,maxDecryptDepth=2,packageNamePattern=r"<title>([^<]+)</title>",
                 deepPattern=r"(https?://[^\"'<>\\s]+)"),
      build_rule("Example direct HTTP",r"https?://downloads\.example\.org/files/[^?#]+","DIRECTHTTP",logging=True),
      build_rule("Example follow redirect",r"https?://example\.org/go/[^?#]+","FOLLOWREDIRECT",logging=True),
      build_rule("Example rewrite",r"https?://example\.org/old/(.+)","REWRITE",logging=True,
                 rewriteReplaceWith=r"https://example.org/new/$1"),
      build_rule("Example submit form",r"https?://example\.org/form/[^?#]+","SUBMITFORM",logging=True,
                 formPattern=r"<form[^>]*>(.*?)</form>")
    ]
