#!/usr/bin/env python3
"""
OneDrive Cross-Account Migration CLI (Standalone Tool)
- Commands: plan (dry-run diff), copy, move
- Filters: extensions, modified-after, size range
- CSV/JSON reports; resume cache; skip-identicals
- Free & first-party: Microsoft Graph + MSAL
"""
import argparse, csv, datetime, json, os, pathlib, sys, time
from typing import Dict, List, Optional, Tuple
import msal, requests

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Files.ReadWrite.All"]
CHUNK = 32 * 1024 * 1024
DEFAULT_CONFLICT_BEHAVIOR = "rename"

def device_login(client_id: str, label: str) -> str:
    print(f"[auth] Sign in to {label} account (device code)...")
    app = msal.PublicClientApplication(client_id=client_id, authority="https://login.microsoftonline.com/common")
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError("Failed to start device flow")
    print(f"  Code: {flow['user_code']}\n  Visit: {flow['verification_uri']}")
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Login failed: {result.get('error_description')}")
    return result["access_token"]

def _hdr(tok: str, extra: Optional[Dict[str,str]] = None) -> Dict[str,str]:
    h = {"Authorization": f"Bearer {tok}"}
    if extra: h.update(extra)
    return h

def gget(tok: str, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None, retry: int = 5):
    h = _hdr(tok, headers)
    for i in range(retry):
        r = requests.get(url, params=params, headers=h)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 1)) if r.status_code == 429 else (2 ** i)
            time.sleep(wait); continue
        if r.status_code == 404:
            return r
        r.raise_for_status(); return r
    r.raise_for_status()

def gpost(tok: str, url: str, json_body: Optional[Dict] = None, headers: Optional[Dict] = None):
    h = _hdr(tok, {"Content-Type": "application/json"})
    if headers: h.update(headers)
    r = requests.post(url, json=json_body, headers=h)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {url} failed: {r.status_code} {r.text}")
    return r

def gput(tok: str, url: str, data=None, headers: Optional[Dict] = None):
    h = _hdr(tok, headers)
    r = requests.put(url, data=data, headers=h)
    if r.status_code >= 400:
        raise RuntimeError(f"PUT {url} failed: {r.status_code} {r.text}")
    return r

def gdelete(tok: str, url: str):
    h = _hdr(tok)
    r = requests.delete(url, headers=h)
    if r.status_code >= 400:
        raise RuntimeError(f"DELETE {url} failed: {r.status_code} {r.text}")

def list_children(tok: str, item_path: str = "") -> List[Dict]:
    url = f"{GRAPH}/me/drive/root" + (f":/{item_path}:" if item_path else "") + "/children"
    items = []
    while True:
        r = gget(tok, url, params={"$select": "id,name,size,folder,specialFolder,lastModifiedDateTime", "$top": 200}, headers={"Accept": "application/json"})
        j = r.json() if r.status_code == 200 else {"value": []}
        items.extend(j.get("value", []))
        url = j.get("@odata.nextLink")
        if not url: break
    return items

def is_vault(item: Dict) -> bool:
    sf = item.get("specialFolder")
    return (sf and sf.get("name") == "vault") or item.get("name", "").lower() in {"personal vault","vault"}

def ensure_folder(tok_target: str, rel_path: str):
    if not rel_path: return
    parts = [p for p in pathlib.Path(rel_path).parts if p not in ["", "/"]]
    current = ""
    for p in parts:
        current = f"{current}/{p}" if current else p
        url = f"{GRAPH}/me/drive/root:/{current}"
        r = gget(tok_target, url)
        if r.status_code == 404:
            gpost(tok_target, f"{GRAPH}/me/drive/root/children", json_body={"name": p, "folder": {}, "@microsoft.graph.conflictBehavior": DEFAULT_CONFLICT_BEHAVIOR})

def download_stream(tok_source: str, item_id: str):
    meta = gget(tok_source, f"{GRAPH}/me/drive/items/{item_id}").json()
    dl = meta.get("@microsoft.graph.downloadUrl")
    if not dl: raise RuntimeError("No download URL")
    s = requests.get(dl, stream=True); s.raise_for_status()
    return s.iter_content(chunk_size=CHUNK)

def upload_small(tok_target: str, rel_path: str, content: bytes):
    url = f"{GRAPH}/me/drive/root:/{rel_path}:/content"
    gput(tok_target, url, data=content, headers={"Content-Type": "application/octet-stream"})

def upload_large(tok_target: str, rel_path: str, file_size: int, reader):
    us = gpost(tok_target, f"{GRAPH}/me/drive/root:/{rel_path}:/createUploadSession", json_body={"item": {"@microsoft.graph.conflictBehavior": DEFAULT_CONFLICT_BEHAVIOR}}).json()
    upurl = us["uploadUrl"]; start = 0
    for chunk in reader:
        L = len(chunk); end = start + L - 1
        headers = {"Content-Length": str(L), "Content-Range": f"bytes {start}-{end}/{file_size}"}
        r = requests.put(upurl, data=chunk, headers=headers)
        if r.status_code in (200, 201): return
        elif r.status_code == 202: start = end + 1; continue
        else: raise RuntimeError(f"Upload failed: {r.status_code} {r.text}")

def target_lookup(tok_target: str, rel_path: str, cache: Dict[str, Dict]):
    if rel_path in cache: return cache[rel_path]
    url = f"{GRAPH}/me/drive/root:/{rel_path}"
    r = gget(tok_target, url, params={"$select": "id,name,size,lastModifiedDateTime"})
    if r.status_code == 404: cache[rel_path] = None; return None
    j = r.json(); cache[rel_path] = j; return j

def parse_date(s: str):
    try: return datetime.datetime.strptime(s, "%Y-%m-%d")
    except: return None

def apply_filters(item: Dict, filters: Dict) -> bool:
    if "folder" in item: return True
    name = item.get("name", ""); size = int(item.get("size", 0)); lm = item.get("lastModifiedDateTime")
    exts = filters.get("exts")
    if exts and not any(name.lower().endswith(e.lower()) for e in exts): return False
    if filters.get("modified_after") and lm:
        try:
            dt = datetime.datetime.fromisoformat(lm.replace("Z","+00:00")).replace(tzinfo=None)
            if dt < filters["modified_after"]: return False
        except: pass
    minb = filters.get("min_bytes"); maxb = filters.get("max_bytes")
    if minb is not None and size < minb: return False
    if maxb is not None and size > maxb: return False
    return True

def plan_with_diff(tok_source: str, tok_target: str, source_root: str, target_root: str, filters: Dict, skip_identicals: bool):
    plan = []; stats = {"folders":0, "files":0, "bytes":0, "skipped_vault":0, "skip_existing_same":0, "existing_diff":0, "not_present":0}
    queue = [(source_root,)]; tcache = {}
    while queue:
        (src_rel,) = queue.pop()
        for item in list_children(tok_source, src_rel):
            name = item["name"]; rel_src = f"{src_rel}/{name}" if src_rel else name
            if is_vault(item):
                plan.append({"type":"skip_vault","source": rel_src}); stats["skipped_vault"] += 1; continue
            if "folder" in item:
                rel_tgt_folder = str(pathlib.Path(target_root) / pathlib.Path(rel_src).name) if target_root else pathlib.Path(rel_src).name
                plan.append({"type":"folder","source": rel_src, "target": rel_tgt_folder}); stats["folders"] += 1; queue.append((rel_src,))
            else:
                if not apply_filters(item, filters): continue
                size = int(item.get("size", 0))
                rel_tgt = str(pathlib.Path(target_root) / pathlib.Path(rel_src)) if target_root else rel_src
                tgt = target_lookup(tok_target, rel_tgt, tcache)
                status = "not_present"
                if tgt is None: stats["not_present"] += 1
                else:
                    ts = int(tgt.get("size", 0))
                    if ts == size:
                        status = "exists_same"; stats["skip_existing_same"] += 1 if skip_identicals else 0
                    else:
                        status = "exists_different"; stats["existing_diff"] += 1
                plan.append({"type":"file","source": rel_src, "target": rel_tgt, "size": size, "id": item["id"], "status": status})
                stats["files"] += 1; stats["bytes"] += size
    return plan, stats

def load_cache(path: Optional[str]):
    if not path or not os.path.exists(path): return {"processed_ids": []}
    try: return json.load(open(path, 'r', encoding='utf-8'))
    except: return {"processed_ids": []}

def save_cache(path: Optional[str], cache: Dict):
    if not path: return
    json.dump(cache, open(path, 'w', encoding='utf-8'), indent=2)

def execute(plan: List[Dict], tok_source: str, tok_target: str, mode: str, skip_identicals: bool, cache_path: Optional[str]):
    cache = load_cache(cache_path); processed = set(cache.get("processed_ids", []))
    summary = {"files_copied":0, "bytes_copied":0, "deleted":0, "folders_created":0, "skipped_identical":0, "skipped_vault":0}
    for a in plan:
        if a["type"] == "skip_vault": summary["skipped_vault"] += 1; continue
        if a["type"] == "folder": ensure_folder(tok_target, a["target"]); summary["folders_created"] += 1; continue
        if a["type"] == "file":
            if a["id"] in processed: continue
            if skip_identicals and a.get("status") == "exists_same":
                summary["skipped_identical"] += 1; cache.setdefault("processed_ids", []).append(a["id"]); save_cache(cache_path, cache); continue
            ensure_folder(tok_target, str(pathlib.Path(a["target"]).parent))
            size = a.get("size", 0); reader = download_stream(tok_source, a["id"]) 
            if size < (4 * 1024 * 1024):
                buf = b""; 
                for b in reader: buf += b
                upload_small(tok_target, a["target"], buf)
            else:
                upload_large(tok_target, a["target"], size, reader)
            summary["files_copied"] += 1; summary["bytes_copied"] += size
            if mode == "move": gdelete(tok_source, f"{GRAPH}/me/drive/items/{a['id']}"); summary["deleted"] += 1
            cache.setdefault("processed_ids", []).append(a["id"]); save_cache(cache_path, cache)
    return summary

# CLI -----------------------
def build_parser():
    p = argparse.ArgumentParser(description="OneDrive Cross-Account Migration CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--client-id", required=True)
    common.add_argument("--source-root", default="")
    common.add_argument("--target-root", default="")
    common.add_argument("--conflict", choices=["rename","replace"], default="rename")
    common.add_argument("--skip-identicals", action="store_true")
    common.add_argument("--resume-cache", default=None)
    common.add_argument("--output-prefix", default="migration")
    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("--exts", default=None)
    filters.add_argument("--modified-after", default=None)
    filters.add_argument("--min-mb", type=float, default=None)
    filters.add_argument("--max-mb", type=float, default=None)
    sub.add_parser("plan", parents=[common, filters])
    sub.add_parser("copy", parents=[common, filters])
    sub.add_parser("move", parents=[common, filters])
    return p

def parse_filters(args):
    f = {"exts": None, "modified_after": None, "min_bytes": None, "max_bytes": None}
    if args.exts: f["exts"] = [e.strip() for e in args.exts.split(",") if e.strip()]
    if args.modified_after:
        try: f["modified_after"] = datetime.datetime.strptime(args.modified_after, "%Y-%m-%d")
        except: print("[warn] invalid modified-after; ignored")
    if args.min_mb is not None: f["min_bytes"] = int(args.min_mb * 1024 * 1024)
    if args.max_mb is not None: f["max_bytes"] = int(args.max_mb * 1024 * 1024)
    return f

def main():
    global DEFAULT_CONFLICT_BEHAVIOR
    parser = build_parser(); args = parser.parse_args(); DEFAULT_CONFLICT_BEHAVIOR = args.conflict
    filters = parse_filters(args)
    tok_source = device_login(args.client_id, "SOURCE")
    tok_target = device_login(args.client_id, "TARGET")
    print("[plan] Building plan & diff...")
    plan, stats = plan_with_diff(tok_source, tok_target, args.source_root, args.target_root, filters, args.skip_identicals)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S"); prefix = f"{args.output_prefix}_{ts}"
    json.dump({"timestamp": ts, "cmd": args.cmd, "stats": stats, "preview": plan[:50]}, open(f"{prefix}_plan.json","w",encoding='utf-8'), indent=2)
    rows = []
    for a in plan:
        if a["type"] == "file": rows.append({"type":"file","source":a["source"],"target":a.get("target",""),"size":a.get("size",0),"status":a.get("status","")})
        elif a["type"] == "folder": rows.append({"type":"folder","source":a["source"],"target":a.get("target",""),"size":"","status":""})
        elif a["type"] == "skip_vault": rows.append({"type":"skip_vault","source":a["source"],"target":"","size":"","status":"skipped"})
    with open(f"{prefix}_plan.csv","w",encoding='utf-8',newline='') as f:
        w = csv.DictWriter(f, fieldnames=["type","source","target","size","status"]); w.writeheader(); [w.writerow(r) for r in rows]
    print(f"[plan] Saved {prefix}_plan.json/.csv")
    if args.cmd == "plan": print("[plan] Dry-run only."); return
    print(f"[exec] Starting {args.cmd}...")
    summary = execute(plan, tok_source, tok_target, "move" if args.cmd == "move" else "copy", args.skip_identicals, args.resume_cache)
    json.dump(summary, open(f"{prefix}_result.json","w",encoding='utf-8'), indent=2)
    with open(f"{prefix}_result.csv","w",encoding='utf-8',newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(summary.keys())); w.writeheader(); w.writerow(summary)
    print(f"[exec] Completed. Saved {prefix}_result.json/.csv")

if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: print("\nInterrupted."); sys.exit(130)
    except Exception as e: print(f"\nError: {e}"); sys.exit(1)
