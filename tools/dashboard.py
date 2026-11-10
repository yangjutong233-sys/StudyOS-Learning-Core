#!/usr/bin/env python3
"""学习进度对账页：支持静态HTML；服务模式可通过受控接口补录时长。

用法：
    python tools/dashboard.py --root . --out reports/dashboard/index.html
"""
import argparse
import base64
import glob
import html
import itertools
import json
import os
import re
import sqlite3
import sys
import threading
import uuid
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

LEARNED = ("LEARNING", "ASSESSMENT_PENDING", "CONSOLIDATE", "MASTERED")


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def num(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def load_scopes(root):
    path = root / "config" / "materials.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        config = {}
    return config.get("handoff_scopes", []), config


def textbook_for(point, config):
    for scope in config.get("handoff_scopes", []):
        if scope.get("subject") != point.get("subject"):
            continue
        catalog = scope.get("catalog_id")
        if catalog and catalog != point.get("catalog_id"):
            continue
        prefix = scope.get("prefix") or ""
        if prefix and not str(point.get("external_id", "")).startswith(prefix):
            continue
        if scope.get("textbook"):
            return scope["textbook"]
    return point.get("catalog_name") or ""


def scope_condition(scope):
    if scope.get("catalog_id"):
        return "k.catalog_id = ?", [scope["catalog_id"]]
    if scope.get("prefix"):
        prefix = scope["prefix"]
        return "c.subject = ? AND substr(k.external_id, 1, ?) = ?", [scope["subject"], len(prefix), prefix]
    return "c.subject = ?", [scope["subject"]]


def one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def chapter_key(external_id):
    parts = external_id.split("-")
    if parts[0] in ("DS", "CO", "OS", "NW", "LA", "EN") and len(parts) >= 3:
        return "-".join(parts[:2])
    return parts[0]


def chapter_label(key):
    match = re.match(r"^(DS|CO|OS|NW)-(\d+)$", key)
    if match:
        return f"第 {int(match.group(2))} 章"
    match = re.match(r"^LA-C(\d+)$", key)
    if match:
        return f"第 {int(match.group(1))} 章"
    match = re.match(r"^K(\d+)$", key)
    if match:
        return f"第 {int(match.group(1))} 章"
    if key == "EN-VOC":
        return "词汇"
    if key == "EN-GRAM":
        return "语法"
    if key == "A":
        return "上册附录"
    return key


def natural_key(value):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def collect(root):
    db_path = root / "database" / "studyos.sqlite3"
    if not db_path.is_file():
        raise SystemExit(f"数据库不存在：{db_path}")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row

    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    today = date.today()
    today_s = today.isoformat()
    week_s = (today + timedelta(days=7)).isoformat()

    scopes, config = load_scopes(root)
    subjects = []
    graph_scopes = []
    node_index = {}
    node_scope = {}
    eid_index = {}
    teachable_total = 0
    for scope in scopes:
        cond, args = scope_condition(scope)
        base = f"FROM knowledge_points k JOIN catalogs c ON c.id = k.catalog_id WHERE {cond}"
        counts = {r["status"]: r["n"] for r in conn.execute(f"SELECT k.status, COUNT(*) n {base} GROUP BY k.status", args)}
        total = sum(counts.values())
        learned = sum(counts.get(s, 0) for s in LEARNED)
        mastered = counts.get("MASTERED", 0)
        assessed = one(conn, f"SELECT COUNT(DISTINCT m.knowledge_id) FROM mastery_assessments m JOIN knowledge_points k ON k.id = m.knowledge_id JOIN catalogs c ON c.id = k.catalog_id WHERE {cond}", args) or 0
        due = one(conn, f"SELECT COUNT(*) FROM review_tasks r JOIN review_series rs ON rs.id = r.series_id JOIN knowledge_points k ON k.id = rs.knowledge_id JOIN catalogs c ON c.id = k.catalog_id WHERE {cond} AND r.status IN ('PENDING','ACTIVE') AND r.due_date <= ?", args + [today_s]) or 0
        last = one(conn, f"SELECT MAX(r.occurred_at) FROM session_records r JOIN learning_sessions s ON s.id = r.session_id JOIN lesson_tasks t ON t.id = s.task_id AND t.version = s.task_version JOIN knowledge_points k ON k.id = t.knowledge_id JOIN catalogs c ON c.id = k.catalog_id WHERE {cond}", args)
        subjects.append({
            "id": scope.get("id"), "title": scope.get("title") or scope.get("id"),
            "total": total, "learned": learned, "assessed": assessed, "mastered": mastered,
            "due": due, "last": last or "—",
            "unassessed": max(0, total - learned),
        })
        points = [dict(r) for r in conn.execute(
            f"SELECT k.id, k.external_id, k.name, k.status, k.data {base} "
            "ORDER BY COALESCE(json_extract(k.data, '$.source_order'), -1), k.external_id", args)]
        nodes = []
        chapters = {}
        for point in points:
            payload = json.loads(point["data"]) if point["data"] else {}
            node = {"id": point["id"], "eid": point["external_id"], "name": point["name"],
                    "status": point["status"], "order": payload.get("source_order", 0),
                    "criteria": payload.get("criteria", ""), "location": payload.get("location", "")}
            nodes.append(node)
            node_index[node["id"]] = node
            node_scope[node["id"]] = scope.get("id")
            eid_index[(scope.get("id"), node["eid"])] = node["id"]
            chapters.setdefault(chapter_key(node["eid"]), []).append(node)
        eids = [node["eid"] for node in nodes]
        parents = {item for item in eids if any(other != item and other.startswith(item + "-") for other in eids)}
        teachable_total += len(eids) - len(parents)
        chapter_list = []
        for key in sorted(chapters, key=natural_key):
            chapter_nodes = chapters[key]
            chapter_list.append({
                "key": key, "label": chapter_label(key), "total": len(chapter_nodes),
                "learned": sum(1 for n in chapter_nodes if n["status"] in LEARNED),
                "mastered": sum(1 for n in chapter_nodes if n["status"] == "MASTERED"),
                "nodes": chapter_nodes,
            })
        graph_scopes.append({"id": scope.get("id"), "title": scope.get("title") or scope.get("id"),
                             "subject": scope.get("subject"), "chapters": chapter_list, "edges": []})

    edge_map = {scope["id"]: {} for scope in graph_scopes}
    for graph_scope in graph_scopes:
        for chapter in graph_scope["chapters"]:
            for node in chapter["nodes"]:
                parts = node["eid"].split("-")
                if len(parts) >= 3:
                    parent = eid_index.get((graph_scope["id"], "-".join(parts[:-1])))
                    if parent and parent != node["id"]:
                        edge_map[graph_scope["id"]][(parent, node["id"])] = "hierarchy"
    for row in conn.execute("SELECT knowledge_id, prerequisite_id, kind FROM knowledge_dependencies"):
        left, right = row["prerequisite_id"], row["knowledge_id"]
        if left in node_index and right in node_index and node_scope[left] == node_scope[right]:
            edge_map[node_scope[left]][(left, right)] = row["kind"]
    for graph_scope in graph_scopes:
        graph_scope["edges"] = [{"from": left, "to": right, "kind": kind}
                                for (left, right), kind in edge_map[graph_scope["id"]].items()]

    active_id = meta.get("active_task")
    task = current = None
    if active_id:
        task = conn.execute(
            "SELECT * FROM lesson_tasks WHERE id = ? ORDER BY version DESC LIMIT 1", (active_id,)
        ).fetchone()
        if task:
            current = conn.execute(
                "SELECT k.*, c.name AS catalog_name, c.subject AS subject FROM knowledge_points k JOIN catalogs c ON c.id = k.catalog_id WHERE k.id = ?",
                (task["knowledge_id"],),
            ).fetchone()
    current_textbook = textbook_for(dict(current), config) if current else None
    last_record = None
    if task:
        last_record = conn.execute(
            "SELECT * FROM session_records WHERE session_id IN (SELECT id FROM learning_sessions WHERE task_id = ?) ORDER BY rowid DESC LIMIT 1",
            (task["id"],),
        ).fetchone()

    reports = [dict(r) for r in conn.execute(
        "SELECT id, entity_id, status, occurred_at, path FROM artifacts WHERE kind = 'reports' ORDER BY rowid DESC LIMIT 8"
    )]
    accepted = [r for r in reports if r["status"] == "ACCEPTED"]
    pending = [r for r in reports if r["status"] == "PENDING"]

    recent = [dict(r) for r in conn.execute(
        """SELECT r.session_id, r.occurred_at, r.minutes, r.checkpoint, r.data,
                  k.external_id, k.catalog_id, k.name AS knowledge_name, c.name AS catalog_name,
                  c.subject AS subject, json_extract(r.data, '$.time_precision') AS time_precision
           FROM session_records r
           JOIN learning_sessions s ON s.id = r.session_id
           JOIN lesson_tasks t ON t.id = s.task_id AND t.version = s.task_version
           JOIN knowledge_points k ON k.id = t.knowledge_id
           JOIN catalogs c ON c.id = k.catalog_id
           WHERE r.id NOT IN (SELECT json_extract(data, '$.supersedes_record_id')
               FROM session_records WHERE json_extract(data, '$.supersedes_record_id') IS NOT NULL)
           ORDER BY r.rowid DESC LIMIT 10"""
    )]

    reviews = [dict(r) for r in conn.execute(
        """SELECT r.id, r.round, r.due_date, r.status, k.external_id, k.catalog_id,
                  k.name AS knowledge_name, c.name AS catalog_name, c.subject AS subject
           FROM review_tasks r JOIN review_series rs ON rs.id = r.series_id
           JOIN knowledge_points k ON k.id = rs.knowledge_id
           JOIN catalogs c ON c.id = k.catalog_id
           WHERE r.status IN ('PENDING','ACTIVE')
           ORDER BY r.due_date, k.external_id LIMIT 30"""
    )]
    cancelled_reviews = [dict(r) for r in conn.execute(
        """SELECT r.id, r.round, r.due_date, r.status, k.external_id, k.catalog_id,
                  k.name AS knowledge_name, c.name AS catalog_name, c.subject AS subject
           FROM review_tasks r JOIN review_series rs ON rs.id = r.series_id
           JOIN knowledge_points k ON k.id = rs.knowledge_id
           JOIN catalogs c ON c.id = k.catalog_id
           WHERE r.status='CANCELLED'
           ORDER BY r.due_date DESC, k.external_id LIMIT 30"""
    )]
    for item in recent + reviews + cancelled_reviews:
        item["textbook"] = textbook_for(item, config)
    review_pending = one(conn, "SELECT COUNT(*) FROM review_tasks WHERE status IN ('PENDING','ACTIVE')") or 0
    overdue = one(conn, "SELECT COUNT(*) FROM review_tasks WHERE status IN ('PENDING','ACTIVE') AND due_date < ?", (today_s,)) or 0
    due_today = one(conn, "SELECT COUNT(*) FROM review_tasks WHERE status IN ('PENDING','ACTIVE') AND due_date = ?", (today_s,)) or 0
    upcoming = one(conn, "SELECT COUNT(*) FROM review_tasks WHERE status IN ('PENDING','ACTIVE') AND due_date > ? AND due_date <= ?", (today_s, week_s)) or 0
    next_review = one(conn, "SELECT MIN(due_date) FROM review_tasks WHERE status IN ('PENDING','ACTIVE')")
    cancelled = one(conn, "SELECT COUNT(*) FROM review_tasks WHERE status='CANCELLED'") or 0
    series = one(conn, "SELECT COUNT(*) FROM review_series") or 0

    learned_no_assessment = one(conn, f"SELECT COUNT(*) FROM knowledge_points k WHERE k.status IN ({','.join('?' for _ in LEARNED)}) AND NOT EXISTS (SELECT 1 FROM mastery_assessments m WHERE m.knowledge_id = k.id)", LEARNED) or 0
    learned_no_review = one(conn, f"SELECT COUNT(*) FROM knowledge_points k WHERE k.status IN ({','.join('?' for _ in LEARNED)}) AND NOT EXISTS (SELECT 1 FROM review_series rs WHERE rs.knowledge_id = k.id)", LEARNED) or 0
    time_stats = conn.execute("""SELECT
        COALESCE(SUM(CASE WHEN json_extract(r.data, '$.time_precision') IN ('DATE_ONLY','UNKNOWN')
            OR json_extract(r.data, '$.time_precision') IS NULL THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN r.minutes IS NULL THEN 1 ELSE 0 END), 0),
        COALESCE(SUM(r.minutes), 0)
        FROM session_records r WHERE r.id NOT IN
        (SELECT json_extract(data, '$.supersedes_record_id') FROM session_records
         WHERE json_extract(data, '$.supersedes_record_id') IS NOT NULL)""").fetchone()
    date_only, unknown_minutes, confirmed_minutes = time_stats

    integrity = one(conn, "PRAGMA quick_check") or "unknown"
    fk = len(list(conn.execute("PRAGMA foreign_key_check")))
    event_seq = one(conn, "SELECT COALESCE(MAX(event_seq), 0) FROM audit_events") or 0
    schema = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()

    backups = []
    for manifest in glob.glob(str(root / "backups" / "*" / "manifest.json")):
        try:
            data = json.loads(Path(manifest).read_text(encoding="utf-8-sig"))
            backups.append(data.get("created_at") or "")
        except (OSError, ValueError):
            pass
    last_backup = max((b for b in backups if b), default="—")

    configured = missing = 0
    base = Path(config.get("root") or root)
    if not base.is_absolute():
        base = root / base
    for spec in (config.get("subjects") or {}).values():
        if not spec.get("path"):
            continue
        subject_dir = Path(spec["path"])
        if not subject_dir.is_absolute():
            subject_dir = base / subject_dir
        for item in (spec.get("files") or {}).values():
            configured += 1
            target = Path(item.get("path") or "")
            if not target.is_absolute():
                target = subject_dir / target
            if not target.is_file():
                missing += 1

    handoffs = 0
    for scope in scopes:
        directory = root / (scope.get("directory") or "")
        for name in ("CODEX_HANDOFF.md", "GPT_TEACHING_HANDOFF.md"):
            if (directory / name).is_file():
                handoffs += 1

    default_scope = next((s["id"] for s in subjects if s["learned"] > 0), None)
    if not default_scope:
        default_scope = "data-structures" if any(g["id"] == "data-structures" for g in graph_scopes) else (graph_scopes[0]["id"] if graph_scopes else None)
    graph = {"default_scope": default_scope, "scopes": graph_scopes}
    learned_total = sum(item["learned"] for item in subjects)
    coverage = {"learned": learned_total, "teachable": teachable_total,
                "remaining": max(0, teachable_total - learned_total)}

    return {
        "today": today_s, "meta": meta, "event_seq": event_seq, "schema": schema,
        "graph": graph, "coverage": coverage,
        "subjects": subjects, "current": dict(current) if current else None, "current_textbook": current_textbook,
        "task": dict(task) if task else None, "last_record": dict(last_record) if last_record else None,
        "reports": reports, "accepted": len(accepted), "pending": len(pending),
        "recent": recent, "reviews": reviews, "cancelled_reviews": cancelled_reviews,
        "review_pending": review_pending, "overdue": overdue, "due_today": due_today,
        "upcoming": upcoming, "next_review": next_review, "cancelled": cancelled, "series": series,
        "learned_no_assessment": learned_no_assessment, "learned_no_review": learned_no_review,
        "date_only": date_only, "unknown_minutes": unknown_minutes, "confirmed_minutes": confirmed_minutes,
        "integrity": integrity, "fk": fk, "last_backup": last_backup,
        "materials": (configured, missing), "handoffs": handoffs,
        "db_size": db_path.stat().st_size,
    }


GRAPH_CSS = """
.graph-card{margin-top:18px}
.graph-tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.graph-tabs button{background:transparent;border:1px solid var(--line);color:var(--muted);padding:4px 9px;font:12px "Cascadia Mono","Consolas","Microsoft YaHei",monospace;cursor:pointer}
.graph-tabs button.active{color:var(--accent);border-color:rgba(127,178,217,.55)}
.graph-crumbs{display:flex;flex-wrap:wrap;gap:10px;align-items:center;color:var(--muted);font-size:12px;margin-bottom:8px}
.graph-crumbs button{background:transparent;border:0;color:var(--accent);cursor:pointer;font:inherit;padding:0}
.graph-canvas{overflow-x:auto;border:1px solid rgba(126,156,190,.16);background:#070b10}
.graph-canvas svg{display:block}
.graph-detail{min-height:20px;margin-top:10px;color:var(--muted);font-size:12px;line-height:1.8}
.graph-detail b{color:var(--text);font-weight:400}
.graph-badge{display:inline-block;border:1px solid currentColor;padding:0 5px;margin-left:6px;font-size:11px}
.graph-legend{color:var(--muted);font-size:11px;margin-top:8px}
.graph-legend span{margin-right:12px}
.graph-toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:14px 0}
.graph-search{flex:1;min-width:180px;border:1px solid #cbd9e8;border-radius:8px;background:#f5f9fd;color:#263b51;padding:10px 12px;font:inherit}
.graph-tool{border:1px solid #cbd9e8;border-radius:8px;background:#fff;color:#355675;padding:9px 12px;cursor:pointer;font:inherit}
.graph-tool:hover,.graph-tool:focus-visible{background:#e8f2fb;outline:2px solid #77a9d2;outline-offset:1px}
.graph-results{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:10px}.graph-results button,.graph-link{border:1px solid #d4e2ef;background:#edf5fc;color:#315c83;border-radius:6px;padding:5px 9px;cursor:pointer;font:inherit;text-align:left}
.graph-results:empty{display:none}.graph-canvas{max-height:650px;overflow:auto;background:radial-gradient(circle at 50% 20%,#345878,#1a2a40 75%);border-radius:10px;color:#dbe9f8}
.graph-canvas svg{max-width:none}.graph-canvas g[role=button]:focus{outline:none}.graph-canvas g[role=button]:focus circle,.graph-canvas g[role=button]:hover circle{stroke:#d8efff;stroke-width:3}
.graph-overview{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;padding:22px}.graph-chapter{background:linear-gradient(145deg,rgba(109,153,196,.25),rgba(22,43,64,.65));border:1px solid rgba(164,207,240,.3);border-radius:10px;padding:18px;text-align:left;color:#eef7ff;cursor:pointer;font:inherit;transition:transform .15s,border-color .15s}.graph-chapter:hover,.graph-chapter:focus-visible{transform:translateY(-3px);border-color:#addcff;outline:2px solid #addcff;outline-offset:2px}.graph-chapter strong,.graph-chapter small{display:block}.graph-chapter strong{font-size:15px;margin-bottom:10px}.graph-chapter small{color:#bdd0e3}.graph-chapter .bar{display:block;height:4px;background:#405a73;border-radius:4px;margin:12px 0 8px;overflow:hidden}.graph-chapter .bar i{display:block;height:100%;background:#84c8f4}
.graph-detail{background:#f0f5fa;border:1px solid #dbe6f0;border-radius:9px;padding:14px 16px;color:#526a82}.graph-detail b{color:#253b52;font-weight:600}.graph-relations{display:flex;flex-wrap:wrap;gap:6px;margin-top:5px}.graph-relation-title{margin-top:10px;color:#657d94}.graph-link:hover{background:#dbeefa}
.graph-canvas{position:relative;scroll-behavior:smooth;background:radial-gradient(ellipse at 50% 25%,#365c80 0,#1b304b 48%,#111e31 100%);min-height:220px}
.graph-canvas svg{background-image:radial-gradient(rgba(184,216,242,.13) 1px,transparent 1px);background-size:24px 24px}
.graph-canvas:has(svg){cursor:grab}.graph-canvas.is-panning{cursor:grabbing;user-select:none}
.graph-node .orb-halo{opacity:.13;transition:opacity .2s,r .2s}.graph-node .orb-ring{opacity:.65;transition:opacity .2s,stroke-width .2s}.graph-node .orb-core{transition:filter .2s,stroke-width .2s}.graph-node:hover .orb-halo,.graph-node:focus .orb-halo,.graph-node.is-selected .orb-halo{opacity:.35}.graph-node:hover .orb-ring,.graph-node:focus .orb-ring,.graph-node.is-selected .orb-ring{opacity:1;stroke-width:2}.graph-node:hover .orb-core,.graph-node:focus .orb-core,.graph-node.is-selected .orb-core{filter:url(#orb-glow)}
.graph-node text{pointer-events:none}.graph-edge{transition:opacity .18s}.graph-tooltip{position:fixed;z-index:20;max-width:280px;padding:10px 13px;pointer-events:none;background:#f9fcff;color:#253c57;border:1px solid #bdd4e9;border-radius:9px;box-shadow:0 12px 28px #0b172b55;font-size:12px;line-height:1.6}.graph-tooltip[hidden]{display:none}.graph-tooltip strong{display:block;color:#1b3857;font-size:13px}.graph-zoom-value{min-width:42px;text-align:center;color:#5b748d;font-size:12px}
.graph-detail{border-left:3px solid #9a424d}.graph-detail-heading{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.graph-detail-sub{color:#73889d;font-size:12px;margin-top:5px}.graph-badge{border-radius:99px;padding:2px 9px;background:#e2edf8;border:0;margin-left:0}
.course-content{margin:14px 0;padding:15px 17px;border-radius:9px;background:#fff;border:1px solid #d6e4f0;line-height:1.85}.course-content h4{margin:0 0 7px;color:#274b70;font-size:14px}.course-content p{margin:0;white-space:pre-wrap;color:#334d66}.course-content details{margin-top:12px}.course-content summary{color:#994b50}.course-content .course-source{margin-top:10px;font-size:11px;color:#72879b;overflow-wrap:anywhere}
@media(prefers-reduced-motion:reduce){.graph-node *, .graph-edge{transition:none!important}.graph-canvas{scroll-behavior:auto}}
"""

GRAPH_JS = r"""
(function(){
  const data = JSON.parse(document.getElementById('graph-data').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const COLORS = {MASTERED:'#5fbf9f',LEARNING:'#7fb2d9',ASSESSMENT_PENDING:'#d9a35f',CONSOLIDATE:'#b08fd9',UNASSESSED:'#8298ae',UNLEARNED:'#64798f'};
  const LABELS = {MASTERED:'已掌握',LEARNING:'学习中',ASSESSMENT_PENDING:'待考核',CONSOLIDATE:'待巩固',UNASSESSED:'未评估',UNLEARNED:'未学'};
  let scopeId = data.default_scope || (data.scopes[0] && data.scopes[0].id);
  let chapterKey = null;
  let selected = null;
  const tabs = document.getElementById('graph-tabs');
  const crumbs = document.getElementById('graph-crumbs');
  const canvas = document.getElementById('graph-canvas');
  const detail = document.getElementById('graph-detail');
  const search = document.getElementById('graph-search');
  const results = document.getElementById('graph-results');
  const tooltip = document.getElementById('graph-tooltip');
  const zoomValue = document.getElementById('graph-zoom-value');
  let zoom = 1;
  let pan = null;
  canvas.addEventListener('pointerdown', function(event){
    if(event.button !== 0 || !canvas.querySelector('svg') || event.target.closest('.graph-node')){return;}
    pan={x:event.clientX,y:event.clientY,left:canvas.scrollLeft,top:canvas.scrollTop}; canvas.setPointerCapture(event.pointerId); canvas.classList.add('is-panning');
  });
  canvas.addEventListener('pointermove', function(event){if(pan){canvas.scrollLeft=pan.left-(event.clientX-pan.x); canvas.scrollTop=pan.top-(event.clientY-pan.y);}});
  function endPan(){pan=null; canvas.classList.remove('is-panning');}
  canvas.addEventListener('pointerup',endPan); canvas.addEventListener('pointercancel',endPan); canvas.addEventListener('lostpointercapture',endPan);

  function goNode(id){
    const target = data.scopes.flatMap(function(s){return (s.chapters || []).map(function(c){return {scope:s, chapter:c};});}).find(function(item){return item.chapter.nodes.some(function(n){return n.id === id;});});
    if(!target){return;}
    scopeId = target.scope.id; chapterKey = target.chapter.key; selected = id;
    search.value = ''; clear(results); render();
    centerSelected();
  }
  search.addEventListener('input', function(){
    clear(results); const q = search.value.trim().toLowerCase(); if(!q){return;}
    const matches = [];
    data.scopes.forEach(function(s){(s.chapters || []).forEach(function(c){(c.nodes || []).forEach(function(n){if((n.name + ' ' + n.eid).toLowerCase().includes(q)){matches.push({node:n, scope:s});}});});});
    if(!matches.length){results.textContent = '没有找到匹配的知识点'; return;}
    matches.slice(0, 12).forEach(function(item){const button = document.createElement('button'); button.type='button'; button.textContent=item.scope.title + ' · ' + item.node.eid + ' ' + item.node.name; button.onclick=function(){goNode(item.node.id);}; results.appendChild(button);});
    if(matches.length > 12){const note=document.createElement('span'); note.textContent='仅显示前 12 项，请输入更具体的关键词'; results.appendChild(note);}
  });
  function setZoom(value){zoom = Math.max(.6, Math.min(2, value)); render(); if(selected){centerSelected();}}
  document.getElementById('graph-zoom-in').onclick = function(){setZoom(zoom + .2);};
  document.getElementById('graph-zoom-out').onclick = function(){setZoom(zoom - .2);};
  document.getElementById('graph-zoom-reset').onclick = function(){setZoom(1);};
  function centerSelected(){const active=canvas.querySelector('.graph-node.is-selected'); if(active){const box=active.getBoundingClientRect(), parent=canvas.getBoundingClientRect(); canvas.scrollBy({left:box.left + box.width/2 - parent.left - parent.width/2, top:box.top + box.height/2 - parent.top - parent.height/2, behavior:'smooth'});}}
  function hideTooltip(){tooltip.hidden=true;}
  function showTooltip(node, event){
    clear(tooltip); const name=document.createElement('strong'); name.textContent=node.name; tooltip.appendChild(name);
    const meta=document.createElement('span'); meta.textContent=node.eid + ' · ' + statusLabel(node.status) + ' · 点击查看关联'; tooltip.appendChild(meta);
    tooltip.hidden=false; moveTooltip(event);
  }
  function moveTooltip(event){if(!tooltip.hidden){tooltip.style.left=Math.max(8,Math.min(event.clientX+16,window.innerWidth-tooltip.offsetWidth-8))+'px'; tooltip.style.top=Math.max(8,Math.min(event.clientY+16,window.innerHeight-tooltip.offsetHeight-8))+'px';}}

  function esc(value){return String(value == null ? '' : value).replace(/[&<>"']/g, function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function shortName(value, limit){const text = String(value == null ? '' : value); return text.length > limit ? text.slice(0, limit) + '…' : text;}
  function scope(){return data.scopes.find(function(s){return s.id === scopeId;}) || data.scopes[0];}
  function chapter(){return (scope().chapters || []).find(function(c){return c.key === chapterKey;});}
  function color(status){return COLORS[status] || COLORS.UNASSESSED;}
  function statusLabel(status){return LABELS[status] || '未评估';}
  function clear(node){while(node.firstChild){node.removeChild(node.firstChild);}}
  function svgEl(tag, attrs){
    const node = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function(key){node.setAttribute(key, attrs[key]);});
    return node;
  }
  function textEl(x, y, value, attrs){
    const node = svgEl('text', Object.assign({x:x, y:y, fill:'#c8d4e3', 'font-size':11, 'text-anchor':'middle'}, attrs || {}));
    node.textContent = value;
    return node;
  }
  function nodeById(scopeValue, id){
    for(const chapterValue of scopeValue.chapters || []){
      for(const node of chapterValue.nodes || []){if(node.id === id){return node;}}
    }
    return null;
  }
  function renderTabs(){
    clear(tabs);
    data.scopes.forEach(function(scopeValue){
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = scopeValue.title;
      if(scopeValue.id === scopeId){button.className = 'active';}
      button.onclick = function(){scopeId = scopeValue.id; chapterKey = null; selected = null; render();};
      tabs.appendChild(button);
    });
  }
  function renderCrumbs(){
    clear(crumbs);
    const title = document.createElement('span');
    title.textContent = scope().title;
    crumbs.appendChild(title);
    if(chapterKey){
      const separator = document.createElement('span');
      separator.textContent = '/';
      crumbs.appendChild(separator);
      const label = document.createElement('span');
      label.textContent = chapter().label;
      crumbs.appendChild(label);
      const back = document.createElement('button');
      back.type = 'button';
      back.textContent = '← 返回章节';
      back.onclick = function(){chapterKey = null; selected = null; render();};
      crumbs.appendChild(back);
      const chapters = scope().chapters || [];
      const index = chapters.findIndex(function(item){return item.key === chapterKey;});
      if(index > 0){
        const previous = document.createElement('button');
        previous.type = 'button';
        previous.textContent = '← 上一章';
        previous.onclick = function(){chapterKey = chapters[index - 1].key; selected = null; render();};
        crumbs.appendChild(previous);
      }
      if(index >= 0 && index < chapters.length - 1){
        const next = document.createElement('button');
        next.type = 'button';
        next.textContent = '下一章 →';
        next.onclick = function(){chapterKey = chapters[index + 1].key; selected = null; render();};
        crumbs.appendChild(next);
      }
    }else{
      const hint = document.createElement('span');
      hint.textContent = '点击章节展开知识点';
      crumbs.appendChild(hint);
    }
  }
  function render(){
    hideTooltip(); zoomValue.textContent = Math.round(zoom * 100) + '%';
    renderTabs();
    renderCrumbs();
    clear(canvas);
    clear(detail);
    const current = chapter();
    if(current){drawChapter(current);}else{drawChapters();}
    if(selected){const node=nodeById(scope(), selected); if(node){renderDetail(node);}}
    if(!selected){detail.textContent = current ? '悬停查看提示，点击知识点探索关联；拖动空白处可移动图谱，也可用 Tab 与回车操作。' : '选择章节展开知识点，或搜索名称与编号。';}
  }
  function drawChapters(){
    const scopeValue = scope();
    const chapters = scopeValue.chapters || [];
    if(!chapters.length){canvas.textContent = '该科目暂无知识点。'; return;}
    const overview = document.createElement('div'); overview.className = 'graph-overview';
    chapters.forEach(function(item){
      const button = document.createElement('button'); button.type = 'button'; button.className = 'graph-chapter';
      const title = document.createElement('strong'); title.textContent = item.label;
      const info = document.createElement('small'); info.textContent = item.total + ' 个知识点 · ' + item.learned + ' 个学过';
      const bar = document.createElement('span'); bar.className = 'bar'; const fill = document.createElement('i'); fill.style.width = (item.total ? item.learned / item.total * 100 : 0) + '%'; bar.appendChild(fill);
      const status = document.createElement('small'); status.textContent = '已掌握 ' + item.mastered + ' · 点击展开 →';
      button.append(title, info, bar, status); button.onclick = function(){chapterKey = item.key; selected = null; zoom = 1; render();}; overview.appendChild(button);
    });
    canvas.appendChild(overview);
  }
  function computeLevels(nodes, edges){
    const incoming = {};
    const outgoing = {};
    nodes.forEach(function(node){incoming[node.id] = 0; outgoing[node.id] = [];});
    edges.forEach(function(edge){
      if(incoming[edge.to] !== undefined && outgoing[edge.from]){
        incoming[edge.to] += 1;
        outgoing[edge.from].push(edge.to);
      }
    });
    const queue = nodes.filter(function(node){return incoming[node.id] === 0;}).map(function(node){return node.id;});
    const levels = {};
    queue.forEach(function(id){levels[id] = 0;});
    for(let index = 0; index < queue.length; index += 1){
      const id = queue[index];
      outgoing[id].forEach(function(target){
        if(levels[target] === undefined){levels[target] = levels[id] + 1;}
        if(incoming[target] > 0){incoming[target] -= 1;}
        if(incoming[target] === 0){queue.push(target);}
      });
    }
    nodes.forEach(function(node){if(levels[node.id] === undefined){levels[node.id] = 0;}});
    return levels;
  }
  function drawChapter(current){
    const scopeValue = scope();
    const nodes = current.nodes || [];
    const ids = new Set(nodes.map(function(node){return node.id;}));
    const edges = (scopeValue.edges || []).filter(function(edge){return ids.has(edge.from) && ids.has(edge.to);});
    if(!nodes.length){canvas.textContent = '本章暂无知识点。'; return;}
    const levels = computeLevels(nodes, edges);
    const buckets = {};
    nodes.forEach(function(node){const level = levels[node.id]; (buckets[level] = buckets[level] || []).push(node);});
    const levelKeys = Object.keys(buckets).map(Number).sort(function(a, b){return a - b;});
    const positions = {};
    let W;
    let H;
    if(levelKeys.length <= 1 && nodes.length > 6){
      const columns = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(nodes.length))));
      const rows = Math.ceil(nodes.length / columns);
      W = Math.max(820, columns * 190 + 100);
      H = Math.max(320, rows * 100 + 80);
      nodes.forEach(function(node, index){
        const row = Math.floor(index / columns);
        const column = index % columns;
        positions[node.id] = {x:90 + column * 190, y:60 + row * 100};
      });
    }else{
      const maxCount = Math.max.apply(null, levelKeys.map(function(level){return buckets[level].length;}));
      W = Math.max(900, levelKeys.length * 190 + 100);
      H = Math.max(340, maxCount * 90 + 90);
      levelKeys.forEach(function(level, column){
        const items = buckets[level];
        items.forEach(function(node, index){
          positions[node.id] = {x:90 + column * 190, y:60 + (H - 120) * (index + 1) / (items.length + 1)};
        });
      });
    }
    const svg = svgEl('svg', {viewBox:'0 0 ' + W + ' ' + H, width:W * zoom, height:H * zoom});
    const defs = svgEl('defs');
    const marker = svgEl('marker', {id:'graph-arrow', markerWidth:8, markerHeight:8, refX:6, refY:4, orient:'auto'});
    marker.appendChild(svgEl('path', {d:'M0,0 L8,4 L0,8 z', fill:'#556b80'}));
    defs.appendChild(marker);
    const glow=svgEl('filter',{id:'orb-glow',x:'-100%',y:'-100%',width:'300%',height:'300%'});
    glow.appendChild(svgEl('feGaussianBlur',{stdDeviation:3,result:'blur'})); const merge=svgEl('feMerge'); merge.appendChild(svgEl('feMergeNode',{in:'blur'})); merge.appendChild(svgEl('feMergeNode',{in:'SourceGraphic'})); glow.appendChild(merge); defs.appendChild(glow);
    svg.appendChild(defs);
    edges.forEach(function(edge){
      const from = positions[edge.from];
      const to = positions[edge.to];
      if(!from || !to){return;}
      const path = svgEl('path', {
        class:'graph-edge',
        d:'M' + from.x + ',' + from.y + ' C' + (from.x + 60) + ',' + from.y + ' ' + (to.x - 60) + ',' + to.y + ' ' + to.x + ',' + to.y,
        fill:'none',
        stroke: selected && (edge.from === selected || edge.to === selected) ? '#b9e9ff' : (edge.kind === 'strong' ? '#7fb2d9' : (edge.kind === 'weak' ? '#718ca4' : '#617b92')),
        'stroke-opacity': selected && edge.from !== selected && edge.to !== selected ? .25 : .85,
        'stroke-width': selected && (edge.from === selected || edge.to === selected) ? 2.5 : (edge.kind === 'strong' ? 1.6 : 1),
        'stroke-dasharray': edge.kind === 'hierarchy' ? '4 4' : (edge.kind === 'weak' ? '2 4' : 'none'),
        'marker-end':'url(#graph-arrow)'
      });
      svg.appendChild(path);
    });
    nodes.forEach(function(node){
      const point = positions[node.id];
      const group = svgEl('g', {class:'graph-node' + (node.id === selected ? ' is-selected' : ''), style:'cursor:pointer', tabindex:0, role:'button', 'aria-pressed':node.id === selected ? 'true' : 'false', 'aria-label':node.eid + ' ' + node.name + ' ' + statusLabel(node.status)});
      group.appendChild(svgEl('circle',{class:'orb-halo',cx:point.x,cy:point.y,r:25,fill:color(node.status)}));
      group.appendChild(svgEl('circle',{class:'orb-ring',cx:point.x,cy:point.y,r:19,fill:'none',stroke:color(node.status),'stroke-width':1}));
      group.appendChild(svgEl('circle', {
        class:'orb-core', cx:point.x, cy:point.y, r:11,
        fill:color(node.status),
        stroke: node.id === selected ? '#e6eef8' : '#0b1118',
        'stroke-width': node.id === selected ? 3 : 2
      }));
      group.appendChild(textEl(point.x, point.y + 28, shortName(node.name, 12), {'font-size':10, fill:'#c8d4e3'}));
      const tip = svgEl('title');
      tip.textContent = node.eid + ' · ' + node.name + ' · ' + statusLabel(node.status);
      group.appendChild(tip);
      group.onmouseenter = function(event){showTooltip(node,event);}; group.onmousemove=moveTooltip; group.onmouseleave=hideTooltip;
      group.onfocus = function(){group.classList.add('is-focused');}; group.onblur=hideTooltip;
      group.onclick = function(){selected = node.id; const left=canvas.scrollLeft,top=canvas.scrollTop; render(); canvas.scrollLeft=left; canvas.scrollTop=top;};
      group.onkeydown = function(event){if(event.key === 'Enter' || event.key === ' '){event.preventDefault(); group.onclick(); const active=canvas.querySelector('.graph-node.is-selected'); if(active){active.focus();}}};
      svg.appendChild(group);
    });
    canvas.appendChild(svg);
  }
  function renderDetail(node){
    const scopeValue = scope();
    const incoming = (scopeValue.edges || []).filter(function(edge){return edge.to === node.id;})
      .map(function(edge){return {edge:edge, node:nodeById(scopeValue, edge.from)};}).filter(function(item){return item.node;});
    const outgoing = (scopeValue.edges || []).filter(function(edge){return edge.from === node.id;})
      .map(function(edge){return {edge:edge, node:nodeById(scopeValue, edge.to)};}).filter(function(item){return item.node;});
    clear(detail);
    const header=document.createElement('div'); header.className='graph-detail-heading'; const heading=document.createElement('b'); heading.textContent=node.name; header.appendChild(heading);
    const badge=document.createElement('span'); badge.className='graph-badge'; badge.textContent=statusLabel(node.status); badge.style.color='#365f85'; header.appendChild(badge); detail.appendChild(header);
    const sub=document.createElement('div'); sub.className='graph-detail-sub'; sub.textContent=node.eid + '  /  点击关联知识点继续探索'; detail.appendChild(sub);
    const course=document.createElement('section'); course.className='course-content'; course.setAttribute('aria-live','polite'); detail.appendChild(course);
    function showCourse(info){
      clear(course); const title=document.createElement('h4'); title.textContent='本节讲什么'; course.appendChild(title);
      const body=document.createElement('p'); body.textContent=info.content || info.message || '暂无可用的课程内容。'; course.appendChild(body);
      if(info.goal){const goal=document.createElement('details'); const summary=document.createElement('summary'); summary.textContent='学习目标'; const p=document.createElement('p'); p.textContent=info.goal; goal.append(summary,p); course.appendChild(goal);}
      if(node.criteria){const criteria=document.createElement('details'); const summary=document.createElement('summary'); summary.textContent='大纲中的掌握标准'; const p=document.createElement('p'); p.textContent=node.criteria; criteria.append(summary,p); course.appendChild(criteria);}
      const source=document.createElement('div'); source.className='course-source'; source.textContent=info.source || '来源：数据库中的知识点记录'; course.appendChild(source);
    }
    if(location.protocol === 'http:' || location.protocol === 'https:'){
      showCourse({message:'正在读取本节大纲…'});
      fetch('/api/knowledge?id=' + encodeURIComponent(node.id)).then(function(response){if(!response.ok){throw new Error('读取失败');} return response.json();}).then(function(info){if(selected === node.id && course.isConnected){showCourse(info);}}).catch(function(){if(course.isConnected){showCourse({message:'无法读取本节大纲，请检查本机服务是否仍在运行。'});}});
    }else{showCourse({message:'请通过 dashboard.bat 打开本机页面，以按需读取本节大纲。'});}
    function addRelations(label, items){
      const title=document.createElement('div'); title.className='graph-relation-title'; title.textContent=label + ' · ' + items.length; detail.appendChild(title);
      const list=document.createElement('div'); list.className='graph-relations';
      if(!items.length){list.textContent='暂无登记关联';}
      items.forEach(function(item){const button=document.createElement('button'); button.type='button'; button.className='graph-link'; button.textContent=item.node.eid + ' ' + item.node.name + (item.edge.kind === 'hierarchy' ? '（层级）' : item.edge.kind === 'weak' ? '（弱依赖）' : '（强依赖）'); button.onclick=function(){goNode(item.node.id);}; list.appendChild(button);}); detail.appendChild(list);
    }
    addRelations('前置', incoming); addRelations('后续', outgoing);
  }
  render();
})();
"""


DURATION_CSS = """
.duration-card{margin-top:0}
.duration-form{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.duration-form select,.duration-form input{color-scheme:light;background:#fff;color:#26364b;border:1px solid #a9bbce;border-radius:6px;padding:7px 9px;font:12px "Cascadia Mono","Consolas","Microsoft YaHei",monospace;min-width:0}
.duration-form option{background:#fff;color:#26364b}
.duration-form input::placeholder{color:#64768d;opacity:1}
.duration-form select:focus-visible,.duration-form input:focus-visible{outline:2px solid #477ba8;outline-offset:2px}
.duration-form select{max-width:100%;flex:1 1 320px}
.duration-form input[type=number]{width:90px}
.duration-form input[type=text]{flex:1 1 180px}
.duration-form button{border:1px solid #7fb2d9;background:#122335;color:#cfe6f7;padding:7px 14px;font:12px inherit;cursor:pointer}
.duration-form button:hover{background:#1a334b}
.duration-status{color:var(--muted);font-size:12px;margin-top:8px;min-height:18px}
.duration-status.ok{color:#5fbf9f}
.duration-status.err{color:#d9a35f}
"""

REVIEW_CSS = """
.review-filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:10px}
.review-filter{background:transparent;border:1px solid var(--line);color:var(--muted);padding:4px 9px;font:12px "Cascadia Mono","Consolas","Microsoft YaHei",monospace;cursor:pointer}
.review-filter.active{color:var(--accent);border-color:rgba(127,178,217,.55)}
.review-filter b{color:var(--text);font-weight:400}
.review-filter.active b{color:var(--accent)}
.review-earliest{color:var(--muted);font-size:12px;margin-left:auto}
"""

REVIEW_JS = r"""
(function(){
  const buttons = Array.from(document.querySelectorAll('.review-filter'));
  const rows = Array.from(document.querySelectorAll('#reviews tbody tr[data-state]'));
  const empty = document.getElementById('review-empty');
  function apply(filter){
    let visible = 0;
    rows.forEach(function(row){
      const show = filter === 'all' ? row.dataset.state !== 'cancelled' : row.dataset.state === filter;
      row.hidden = !show;
      if(show){visible += 1;}
    });
    if(empty){empty.hidden = visible > 0;}
    buttons.forEach(function(button){button.classList.toggle('active', button.dataset.filter === filter);});
  }
  buttons.forEach(function(button){button.addEventListener('click', function(){apply(button.dataset.filter);});});
  apply('all');
})();
"""

DURATION_JS = r"""
(function(){
  const form=document.getElementById('duration-form');
  if(!form) return;
  const status=document.getElementById('duration-status');
  const button=form.querySelector('button');
  function setStatus(text, cls){status.textContent=text; status.className='duration-status'+(cls?(' '+cls):'');}
  if(location.protocol!=='http:' && location.protocol!=='https:'){
    setStatus('请通过 dashboard.bat 打开页面后再保存时长。','err');
    button.disabled=true;
    return;
  }
  form.addEventListener('submit', async function(event){
    event.preventDefault();
    const session=document.getElementById('duration-session').value;
    const minutes=Number(document.getElementById('duration-minutes').value);
    const reason=(document.getElementById('duration-reason').value || '手动补录').trim();
    if(!session){setStatus('请选择一次学习记录。','err');return;}
    if(!Number.isFinite(minutes) || minutes<=0 || minutes>1440){setStatus('分钟数需在 1–1440 之间。','err');return;}
    button.disabled=true; setStatus('保存中…');
    try{
      const response=await fetch('/api/duration',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:session,minutes:minutes,reason:reason})});
      const payload=await response.json().catch(function(){return {};});
      if(!response.ok || !payload.ok){
        const message=(payload.error && payload.error.message) || ('保存失败（HTTP '+response.status+'）');
        setStatus(message,'err'); button.disabled=false; return;
      }
      setStatus('已保存，正在刷新…','ok');
      setTimeout(function(){location.reload();},500);
    }catch(error){
      setStatus('保存失败：'+error.message,'err'); button.disabled=false;
    }
  });
})();
"""


def render(data):
    hero_path = Path(__file__).resolve().parent / "assets" / "academy-header.png"
    hero = base64.b64encode(hero_path.read_bytes()).decode("ascii") if hero_path.is_file() else ""
    library_path = Path(__file__).resolve().parent / "assets" / "academy-library.png"
    library = base64.b64encode(library_path.read_bytes()).decode("ascii") if library_path.is_file() else ""
    subjects = "".join(
        f"<tr><td class='name'>{esc(s['title'])}</td><td class='n'>{num(s['total'])}</td>"
        f"<td class='n'>{num(s['learned'])}</td><td class='n'>{num(s['assessed'])}</td>"
        f"<td class='n strong'>{num(s['mastered'])}</td><td class='n'>{num(s['due'])}</td>"
        f"<td class='date'>{esc(s['last'])}</td></tr>"
        for s in data["subjects"]
    )
    current = data["current"]
    task = data["task"]
    if current:
        status = current.get("status") or "UNASSESSED"
        minutes = data["last_record"]["minutes"] if data["last_record"] else None
        minutes_text = f"{int(minutes)} 分钟" if isinstance(minutes, (int, float)) else "未知"
        current_html = f"""
        <div class="card current">
          <div class="current-kicker">THE LEARNING ARCHIVE <span>✦</span>  学习档案</div>
          <div class="label">当前任务</div>
          <div class="big">{esc(current['external_id'])} · {esc(current['name'])}</div>
          <div class="meta">状态 <b>{esc(status)}</b> · 教材 {esc(data['current_textbook'] or current['catalog_name'])}</div>
          <div class="meta">最近学习 {esc(data['last_record']['occurred_at'] if data['last_record'] else '—')} · 本次时长 {minutes_text}</div>
          <div class="note">断点：{esc(data['last_record']['checkpoint'] if data['last_record'] else '无记录')}</div>
        </div>"""
    else:
        current_html = "<div class='card current'><div class='label'>当前任务</div><div class='note'>尚未选定任务。</div></div>"
    sessions = {}
    for item in data["recent"]:
        if item.get("session_id"):sessions.setdefault(item["session_id"],item)
    if sessions:
        options = "".join(
            f"<option value='{esc(session_id)}'>{esc(item['occurred_at'])} · {esc(item['external_id'])} {esc(item['knowledge_name'])}</option>"
            for session_id,item in sessions.items())
        duration_html = f"""<style>{DURATION_CSS}</style>
        <div class="card duration-card">
          <div class="label">补录学习时长</div>
          <form id="duration-form" class="duration-form">
            <select id="duration-session" required>{options}</select>
            <input id="duration-minutes" type="number" min="1" max="1440" step="1" placeholder="分钟" required>
            <input id="duration-reason" type="text" maxlength="120" value="手动补录">
            <button type="submit">保存</button>
          </form>
          <div id="duration-status" class="duration-status">保存会追加一条时长更正，原记录保留。</div>
        </div>
        <script>{DURATION_JS}</script>"""
    else:
        duration_html = ""

    recent = "".join(
        f"<tr><td class='date'>{esc(r['occurred_at'])}</td><td>{esc(r.get('textbook') or r['catalog_name'])}</td>"
        f"<td class='name'>{esc(r['external_id'])} {esc(r['knowledge_name'])}</td>"
        f"<td>{esc((r['checkpoint'] or '')[:90])}</td><td class='n'>{esc('未知' if r['minutes'] is None else int(r['minutes']))}</td></tr>"
        for r in data["recent"]
    ) or "<tr><td colspan='5' class='empty'>还没有学习批次。</td></tr>"

    today_date = date.fromisoformat(data["today"])
    def review_state(item):
        delta = (today_date - date.fromisoformat(item["due_date"])).days
        if delta > 0:
            return f"已逾期 {delta} 天", "overdue"
        if delta == 0:
            return "今日", "today"
        return f"{abs(delta)} 天后", "upcoming"
    review_rows = []
    for item in data["reviews"]:
        label, state = review_state(item)
        review_rows.append(
            f"<tr data-state='{state}'><td class='date'>{esc(item['due_date'])}</td><td>{esc(item.get('textbook') or item['catalog_name'])}</td>"
            f"<td>{esc(item['external_id'])}</td><td>{esc(item['knowledge_name'])}</td>"
            f"<td class='n'>第 {esc(item['round'])} 轮 · {esc(label)}</td></tr>")
    for item in data["cancelled_reviews"]:
        review_rows.append(
            f"<tr data-state='cancelled' hidden><td class='date'>{esc(item['due_date'])}</td><td>{esc(item.get('textbook') or item['catalog_name'])}</td>"
            f"<td>{esc(item['external_id'])}</td><td>{esc(item['knowledge_name'])}</td>"
            f"<td class='n'>第 {esc(item['round'])} 轮 · 已撤销</td></tr>")
    review_rows.append("<tr id='review-empty' hidden><td colspan='5' class='empty'>这个分类下没有任务。</td></tr>")
    due_html = "".join(review_rows)

    report_rows = "".join(
        f"<tr><td class='date'>{esc(r['occurred_at'][:10])}</td><td>{esc(r['status'])}</td>"
        f"<td class='path'>{esc(r['path'])}</td></tr>"
        for r in data["reports"][:5]
    ) or "<tr><td colspan='3' class='empty'>还没有报告。</td></tr>"

    accepted = data["accepted"]
    pending = data["pending"]
    materials_ok = data["materials"][0] - data["materials"][1]
    db_short = (data["meta"].get("database_id") or "")[:8]
    coverage = data["coverage"]
    graph_json = json.dumps(data["graph"], ensure_ascii=False).replace("</", "<\\/")
    graph_block = f"""<style>{GRAPH_CSS}</style>
<div class="card graph-card">
  <div class="label">知识图谱</div>
  <div id="graph-tabs" class="graph-tabs"></div>
  <div class="graph-toolbar"><input id="graph-search" class="graph-search" type="search" placeholder="搜索知识点名称或编号" aria-label="搜索知识点"><button id="graph-zoom-out" class="graph-tool" type="button" aria-label="缩小图谱">−</button><span id="graph-zoom-value" class="graph-zoom-value" aria-live="polite">100%</span><button id="graph-zoom-in" class="graph-tool" type="button" aria-label="放大图谱">＋</button><button id="graph-zoom-reset" class="graph-tool" type="button">重置</button></div>
  <div id="graph-results" class="graph-results" aria-live="polite"></div>
  <div id="graph-crumbs" class="graph-crumbs"></div>
  <div id="graph-canvas" class="graph-canvas"></div>
  <div id="graph-detail" class="graph-detail">点击章节展开知识点；点节点看前后依赖。</div>
  <div id="graph-tooltip" class="graph-tooltip" role="tooltip" hidden></div>
  <div class="graph-legend"><span>实线 强依赖</span><span>虚线 章节层级</span><span>点线 弱依赖</span></div>
  <div class="graph-legend"><span style="color:#60778d">● 未评估</span><span style="color:#526c85">● 未学</span><span style="color:#397ba9">● 学习中</span><span style="color:#9a622a">● 待考核</span><span style="color:#7754a0">● 待巩固</span><span style="color:#347763">● 已掌握</span></div>
  <script id="graph-data" type="application/json">{graph_json}</script>
  <script>{GRAPH_JS}</script>
</div>"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>考研进度台 · {esc(data['today'])}</title>
<style>
:root{{--bg:#05070a;--panel:#0b1118;--line:rgba(126,156,190,.26);--text:#c8d4e3;--muted:#6f8094;--accent:#7fb2d9;--ok:#5fbf9f;--warn:#d9a35f}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 "Cascadia Mono","Consolas","Microsoft YaHei",monospace}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 48px}}
header{{display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;align-items:flex-end;border-bottom:1px solid var(--line);padding-bottom:18px}}
h1{{margin:0;font-size:22px;letter-spacing:1px}} h1 span{{color:var(--accent)}}
.sub{{color:var(--muted);font-size:12px;margin-top:4px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px;font-size:12px;color:var(--muted)}}
.coverage{{margin-top:18px;padding:10px 14px;border:1px solid var(--line);background:var(--panel);color:var(--muted);font-size:12px}}
.coverage b{{color:var(--accent);font-size:17px;font-weight:400}} .coverage span{{margin-left:12px}}
.chip{{border:1px solid var(--line);padding:3px 9px;background:var(--panel)}} .chip.ok{{color:var(--ok);border-color:rgba(95,191,159,.35)}}
.grid{{display:grid;grid-template-columns:1.35fr .65fr;gap:18px;margin-top:22px}}
@media(max-width:860px){{.grid{{grid-template-columns:1fr}}}}
.card{{border:1px solid var(--line);background:var(--panel);padding:16px 18px;margin-bottom:18px}}
.label{{color:var(--muted);font-size:11px;letter-spacing:2px;margin-bottom:8px}}
.big{{font-size:18px;color:#e6eef8;margin-bottom:6px}} .meta{{color:var(--muted);font-size:12px}} .meta b{{color:var(--accent);font-weight:400}}
.note{{margin-top:10px;padding-left:10px;border-left:2px solid var(--line);color:var(--text);font-size:13px}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th{{color:var(--muted);font-weight:400;text-align:left;border-bottom:1px solid var(--line);padding:8px 8px;font-size:11px;letter-spacing:1px}}
td{{border-bottom:1px solid rgba(126,156,190,.12);padding:8px 8px;vertical-align:top}} tr:last-child td{{border-bottom:0}}
.n{{text-align:right;font-variant-numeric:tabular-nums}} .strong{{color:var(--accent)}} .date{{color:var(--muted);white-space:nowrap}} .name{{color:#dce7f4}}
.empty{{color:var(--muted);padding:14px 8px}} .path{{color:var(--muted);font-size:11px;word-break:break-all}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}} @media(max-width:640px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
.kpi{{border:1px solid var(--line);background:var(--panel);padding:12px}} .kpi .v{{font-size:20px;color:#e6eef8}} .kpi .k{{color:var(--muted);font-size:11px;margin-top:2px}}
.health{{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:12px}} .health b{{color:var(--text);font-weight:400}}
footer{{margin-top:28px;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:11px}}
details{{margin-top:8px}} summary{{cursor:pointer;color:var(--accent)}}
/* 深夜学院档案：保留数据的可读性与原有交互。 */
:root{{--bg:#0b1018;--panel:#131a24;--line:rgba(196,165,104,.22);--text:#e4dfd5;--muted:#a6a49f;--accent:#d7b87a;--ok:#83c5a9;--warn:#e1b879}}
body{{font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;background:radial-gradient(ellipse at 75% 0%,#263344 0,transparent 38%),linear-gradient(145deg,#0b1018,#101722 65%,#151923);background-attachment:fixed}}
.wrap{{max-width:1440px;padding:40px 32px 64px}}
header{{position:relative;min-height:165px;align-items:flex-end;padding:25px 0 25px;border-bottom:1px solid rgba(215,184,122,.48)}}
header:before{{content:"STUDYOS  /  LEARNING ARCHIVE";position:absolute;top:0;left:0;color:var(--accent);font:10px Georgia,serif;letter-spacing:.3em}}
header:after{{content:"✦";position:absolute;right:8px;top:2px;color:rgba(215,184,122,.55);font-size:25px}}
h1{{font-family:Georgia,"SimSun",serif;font-size:clamp(30px,4vw,46px);font-weight:400;letter-spacing:.13em;color:#f3ede0}}
.sub{{font-size:12px;letter-spacing:.08em;margin-top:9px}}
.chips{{gap:6px}} .chip{{background:rgba(19,26,36,.7);border-color:var(--line);border-radius:2px;padding:5px 9px}}
.grid{{grid-template-columns:minmax(0,1.35fr) minmax(0,.65fr);gap:22px;margin-top:28px}}
.grid>div{{min-width:0}}
.card{{position:relative;overflow-x:auto;border-color:var(--line);background:linear-gradient(145deg,rgba(27,35,46,.97),rgba(17,23,32,.97));box-shadow:0 12px 35px rgba(0,0,0,.17);padding:22px 24px;margin-bottom:22px}}
.card:before{{content:"";position:absolute;top:0;left:0;width:42px;height:2px;background:var(--accent)}}
.label{{color:var(--accent);font-size:12px;font-weight:600;letter-spacing:.18em;margin-bottom:16px}}
.big{{font-family:Georgia,"Microsoft YaHei",serif;color:#f3ede0;font-size:20px}}
.note{{border-color:var(--accent);color:#cbc5b9;line-height:1.8}}
table{{min-width:460px}} th{{color:#b9aa8e;padding:10px 8px}} td{{border-color:rgba(215,184,122,.1);padding:11px 8px}} tbody tr:hover{{background:rgba(215,184,122,.045)}}
.name{{color:#f0eadf}} .kpi{{background:rgba(8,13,20,.36);padding:14px}} .kpi .v{{color:#f1e4ca;font-family:Georgia,serif;font-size:25px}}
footer{{line-height:1.9}} summary:focus-visible,button:focus-visible{{outline:2px solid var(--accent);outline-offset:3px}}
@media(max-width:860px){{.grid{{grid-template-columns:1fr}} .wrap{{padding:28px 16px 48px}} header{{min-height:145px}}}}
/* 现代幻想学院 */
:root{{--bg:#e8edf3;--panel:#f7f9fc;--line:#d1dbe7;--text:#26364b;--muted:#64768d;--accent:#a3333b;--ok:#347763;--warn:#9a622a}}
body{{background:linear-gradient(145deg,#d6e0eb,#edf1f6 48%,#dbe5f0);color:var(--text)}}
.wrap{{max-width:1440px;padding:24px 30px 56px}}
header{{min-height:230px;border:0;border-radius:16px;padding:30px 32px;overflow:hidden;background:linear-gradient(90deg,rgba(20,35,56,.92),rgba(29,49,75,.42)),url("data:image/png;base64,{hero}") center 52%/cover;box-shadow:0 14px 36px rgba(36,58,84,.2)}}
header:before{{top:27px;left:32px;color:#b8cce1;letter-spacing:.22em}} header:after{{top:23px;right:32px;color:#d4e7fa}}
h1{{color:#fff;font-family:Georgia,"Microsoft YaHei",serif;font-size:40px;letter-spacing:.08em}} h1 span{{color:#d4e8f9}}
.sub{{color:#d2dfed}} .chips{{color:#dce8f3}} .chip{{background:rgba(28,45,67,.7);border-color:rgba(214,231,247,.25);border-radius:6px}} .chip.ok{{color:#a8e5d2}}
.grid{{gap:18px;margin-top:22px}} .card{{background:rgba(250,252,255,.94);border:1px solid #d4dfea;border-radius:12px;box-shadow:0 8px 24px rgba(46,69,96,.08);padding:21px 22px;margin-bottom:18px}}
.card:before{{width:3px;height:23px;top:20px;left:0;background:#b13e48;border-radius:0 3px 3px 0}}
.label{{color:#243a54;font-size:15px;letter-spacing:.04em;margin-bottom:14px}} .big{{color:#21354b}} .meta b,.strong{{color:#326b9e}} .note{{border-color:#a9444d;color:#596e84}}
th{{color:#657b92}} td{{border-color:#e4eaf1}} tbody tr:hover{{background:#edf4fb}} .name{{color:#263b51}}
.kpi{{border:1px solid #dae4ef;border-radius:8px;background:#f0f5fa}} .kpi .v{{color:#2e5b84}}
.graph-card .graph-tabs button{{border-radius:6px;color:#576c82;background:#eff4f9;padding:7px 11px;font-family:inherit}} .graph-card .graph-tabs button.active{{color:#fff;background:#365f85;border-color:#365f85}}
.graph-card .graph-canvas{{border:0;border-radius:9px;background:#1d3048}} .graph-card .graph-detail{{color:#546b83}}
footer{{color:#63788e}} summary{{color:#90414a}}
@media(max-width:860px){{.wrap{{padding:14px 14px 40px}} header{{min-height:200px;padding:48px 20px 22px}} header:before{{left:20px;top:20px}} header:after{{right:20px;top:16px}} h1{{font-size:30px}}}}
/* Academy visual system */
html{{scroll-behavior:smooth}} body{{background:radial-gradient(ellipse at 15% 20%,#f5f8fc,transparent 44%),linear-gradient(140deg,#d6e1ec,#edf2f7 55%,#cbd9e7)}}
.wrap{{max-width:1480px}} header{{min-height:270px;align-items:end;background-position:center 47%;box-shadow:0 18px 40px #203d5d35}}
header h1{{font-size:clamp(32px,4vw,48px);text-shadow:0 3px 18px #06172c88}} header .sub{{letter-spacing:.12em}}
.page-nav{{display:flex;gap:6px;flex-wrap:wrap;align-items:center;padding:10px 12px;margin-top:14px;background:#f8fbfecc;border:1px solid #fff;border-radius:12px;box-shadow:0 6px 22px #38547212}}
.page-nav .nav-mark{{font:600 11px Georgia,serif;color:#a3333b;letter-spacing:.18em;margin:0 15px 0 7px}} .page-nav a{{text-decoration:none;color:#536b83;font-size:13px;padding:8px 13px;border-radius:7px;transition:background .2s,color .2s}} .page-nav a:hover,.page-nav a:focus-visible{{background:#e1ecf7;color:#244b70;outline:2px solid #81aace}}
.card{{border:1px solid #fff;background:linear-gradient(145deg,#fcfdff,#f3f7fb);box-shadow:0 10px 26px #38547213,0 1px 4px #38547212;transition:box-shadow .2s,transform .2s}} .card:hover{{box-shadow:0 14px 30px #38547222,0 1px 4px #38547216}}
.label{{display:flex;align-items:center;gap:10px;font-size:16px}} .label:after{{content:"";height:1px;flex:1;background:linear-gradient(90deg,#d5e2ee,transparent)}}
.current{{background:linear-gradient(120deg,#f7fbff,#e5f0fa);border-color:#d2e3f2}} .current .big{{font-size:22px}} .current .note{{background:#fff9;border-radius:0 8px 8px 0;padding:10px 12px}}
.kpi{{background:linear-gradient(145deg,#eef5fc,#f9fbfe);transition:transform .2s,border-color .2s}} .kpi:hover{{transform:translateY(-3px);border-color:#a9c7e2}}
.health span{{background:#eef4fa;border:1px solid #e2ebf3;border-radius:7px;padding:6px 9px}} .health b{{font-weight:600}}
.graph-card{{scroll-margin-top:16px}} .graph-card .graph-canvas{{background:radial-gradient(ellipse at 50% 25%,#365c80,#1b304b 48%,#111e31)}}
.graph-card .graph-tabs button{{transition:background .18s,transform .18s}} .graph-card .graph-tabs button:hover{{transform:translateY(-2px);background:#dcebf8}} .graph-card .graph-tabs button.active:hover{{background:#365f85}}
@media(max-width:860px){{header{{min-height:210px}} .page-nav .nav-mark{{width:100%;padding:4px 6px}} .page-nav a{{padding:7px 9px}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}} .card,.kpi,.page-nav a{{transition:none}}}}
/* Illustrated academy details */
body{{background:radial-gradient(ellipse at 8% 22%,#e9dccc 0,transparent 27%),radial-gradient(ellipse at 92% 58%,#c0cfe2 0,transparent 35%),linear-gradient(145deg,#dbe4ee,#f1f3f5 46%,#d3dfeb)}}
header{{border:1px solid #9cb2c988;box-shadow:0 18px 45px #263d5a3b}}
header:after{{content:"✦  ·  ✧";font-family:Georgia,serif;font-size:20px;letter-spacing:.25em;color:#ebd6b2}}
.page-nav{{background:linear-gradient(100deg,#213750,#314964);border:1px solid #647c94;box-shadow:0 8px 20px #233c5830}}
.page-nav .nav-mark{{color:#ecc998}} .page-nav a{{color:#e1eaf4}} .page-nav a:hover,.page-nav a:focus-visible{{background:#ffffff20;color:#fff}}
.grid{{margin-top:18px}} .card{{border:1px solid #cbd9e6;background:linear-gradient(145deg,#fffdfb,#f2f6fa);box-shadow:0 10px 26px #344f6d1c}}
.card:before{{background:linear-gradient(#a83d4a,#d69b68);width:4px;height:28px}}
.label{{font-size:16px;color:#243e59}} .label:before{{content:"✦";font-size:12px;color:#ae5352}} .label:after{{background:linear-gradient(90deg,#b7c9d9,transparent)}}
.current{{min-height:225px;border:1px solid #657a91;background:linear-gradient(90deg,rgba(16,27,45,.96),rgba(21,36,56,.77) 51%,rgba(21,36,56,.14)),url("data:image/png;base64,{library}") center 56%/cover;box-shadow:0 12px 30px #1d2c4380;color:#f0f4fa;display:flex;flex-direction:column;justify-content:center}}
.current .current-kicker{{font:10px Georgia,serif;letter-spacing:.2em;color:#d6b78b;margin-bottom:12px}} .current-kicker span{{color:#eac890}}
.current .label{{color:#dce8f4}} .current .label:after{{background:linear-gradient(90deg,#8294a3,transparent)}} .current .big{{color:#fff;font-size:22px;text-shadow:0 2px 8px #0008}} .current .meta{{color:#d5e2ee}} .current .meta b{{color:#a8d8f8}}
.current .note{{background:#0d1b2bc9;color:#e0e8ef;border-color:#d7aa79;max-width:70%;padding:9px 12px}}
#overview{{background:linear-gradient(145deg,#fffdf9,#edf4fa)}} #overview tbody tr:nth-child(4n+1) .name{{border-left:3px solid #bd7e6c}} #overview tbody tr:nth-child(4n+2) .name{{border-left:3px solid #7e9dbb}} #overview tbody tr:nth-child(4n+3) .name{{border-left:3px solid #b99d70}} #overview tbody tr:nth-child(4n) .name{{border-left:3px solid #988aa9}}
#reviews{{background:linear-gradient(140deg,#fcf8f1,#eff4fa);border-color:#e0d3c3}} #reviews .health span{{background:#fffaf3;border-color:#ead9c6}} #reviews .health b{{color:#9e4e48}}
#reports{{background:linear-gradient(145deg,#fbf6f4,#f2f5fa);border-color:#e3d7d9}} #reports .label:before{{content:"◆";color:#9d4954}}
#reviews table,#reports table{{min-width:0;table-layout:fixed}} #reviews th,#reviews td,#reports th,#reports td{{overflow-wrap:anywhere;padding-left:5px;padding-right:5px}} #reviews .date,#reports .date{{white-space:normal}}
#health{{background:linear-gradient(145deg,#edf5f4,#f7f9fb);border-color:#cbdedb}} #health .label:before{{content:"✧";color:#4b8980}}
.kpi:nth-child(1){{background:linear-gradient(145deg,#f7e8e5,#fff9f6);border-color:#ecd1ca}} .kpi:nth-child(2){{background:linear-gradient(145deg,#e1eff5,#f8fbff);border-color:#cde0e9}} .kpi:nth-child(3){{background:linear-gradient(145deg,#f7efde,#fdfbf4);border-color:#e9dbc1}} .kpi:nth-child(4){{background:linear-gradient(145deg,#eae6f3,#faf9fd);border-color:#dcd3e9}}
.graph-card{{border:1px solid #b9cadb;background:linear-gradient(145deg,#f4f8fc,#e6eef6);box-shadow:0 12px 30px #28476627}} .graph-card .label:before{{content:"✧";font-size:18px;color:#a94c55}} .graph-card .graph-canvas{{box-shadow:inset 0 1px 0 #b8ddff40,0 5px 15px #1a314d25}}
footer{{padding:20px 24px;background:#213951;color:#c8d9e8;border:1px solid #5a7189;border-radius:12px}} footer summary{{color:#f0c89f}}
@media(max-width:860px){{.current{{min-height:220px}} .current .note{{max-width:100%}}}}
</style></head><body><div class="wrap">
<header><div><h1>考研进度<span>台</span></h1><div class="sub">{esc(data['today'])} · 学习进度对账 · 数据源 SQLite</div></div>
<div class="chips"><span class="chip">DB {esc(db_short)}</span><span class="chip">事件 {num(data['event_seq'])}</span><span class="chip">schema {esc(data['schema'])}</span>
<span class="chip {'ok' if data['integrity'] == 'ok' else ''}">完整性 {esc(data['integrity'])}</span><span class="chip">备份 {esc(data['last_backup'][:10] if data['last_backup'] != '—' else '—')}</span></div></header>
<div class="coverage">已学 <b>{num(coverage['learned'])}</b> / {num(coverage['teachable'])} 可教学点<span>剩余 {num(coverage['remaining'])}</span><span>（知识点条目，不是课程数；含附录/工具节点）</span></div>
<nav class="page-nav" aria-label="页面导航"><span class="nav-mark">STUDYOS / INDEX</span><a href="#overview">科目总览</a><a href="#current">当前任务</a><a href="#reviews">复习安排</a><a href="#knowledge">知识图谱</a><a href="#records">学习记录</a></nav>

<div class="grid">
<div>
<div id="current">{current_html}</div>
{duration_html}
<div id="overview" class="card"><div class="label">科目总览</div>
<table><thead><tr><th>科目</th><th class="n">总数</th><th class="n">学过</th><th class="n">考核过</th><th class="n">掌握</th><th class="n">到期复习</th><th>最近学习</th></tr></thead><tbody>{subjects}</tbody></table>
<div class="note">学过 ≠ 掌握。叙述报告只记学习事实；独立掌握要经过预存蓝图的真实测试。</div></div>

<div class="card"><div class="label">证据缺口</div><div class="kpis">
<div class="kpi"><div class="v">{num(data['learned_no_assessment'])}</div><div class="k">学过但没考核</div></div>
<div class="kpi"><div class="v">{num(data['learned_no_review'])}</div><div class="k">学过但没复习系列</div></div>
<div class="kpi"><div class="v">{num(data['date_only'])}</div><div class="k">只有日期、没有时刻</div></div>
<div class="kpi"><div class="v">{num(data['unknown_minutes'])}</div><div class="k">时长未知的批次</div></div>
</div></div>

<div id="records" class="card"><div class="label">最近学习记录</div>
<table><thead><tr><th>日期</th><th>教材</th><th>知识点</th><th>断点</th><th class="n">分钟</th></tr></thead><tbody>{recent}</tbody></table></div>
</div>

<div>
<div id="reviews" class="card"><div class="label">复习安排</div>
<style>{REVIEW_CSS}</style>
<div class="review-filters" role="tablist" aria-label="按状态查看复习">
<button class="review-filter" type="button" data-filter="all">待复习 <b>{num(data['review_pending'])}</b></button>
<button class="review-filter" type="button" data-filter="overdue">已逾期 <b>{num(data['overdue'])}</b></button>
<button class="review-filter" type="button" data-filter="today">今日 <b>{num(data['due_today'])}</b></button>
<button class="review-filter" type="button" data-filter="upcoming">未来 7 天 <b>{num(data['upcoming'])}</b></button>
<button class="review-filter" type="button" data-filter="cancelled">已撤销 <b>{num(data['cancelled'])}</b></button>
<span class="review-earliest">最早 {esc(data['next_review'] or '—')}</span>
</div>
<table><thead><tr><th>到期日</th><th>教材</th><th>知识点</th><th>名称</th><th class="n">状态</th></tr></thead><tbody>{due_html}</tbody></table>
<script>{REVIEW_JS}</script>
<div class="note">复习任务是提醒，不是强制考试；不考就一直保留，可延期或按你的安排完成。完成本轮仍需要独立考核证据。</div></div>

<div id="reports" class="card"><div class="label">报告</div>
<div class="health" style="margin-bottom:10px"><span>正式 <b>{num(accepted)}</b></span><span>待核验 <b>{num(pending)}</b></span><span>总批次 <b>{num(len(data['reports']))}</b></span></div>
<table><thead><tr><th>日期</th><th>状态</th><th>原件</th></tr></thead><tbody>{report_rows}</tbody></table></div>

<div id="health" class="card"><div class="label">数据健康</div><div class="health">
<span>库检查 <b>{'ok' if data['integrity'] == 'ok' and data['fk'] == 0 else '需检查'}</b></span>
<span>外键 <b>{num(data['fk'])}</b></span>
<span>材料文件 <b>{num(materials_ok)}/{num(data['materials'][0])}</b></span>
<span>交接文件 <b>{num(data['handoffs'])}/16</b></span>
<span>学习时长 <b>{num(data['confirmed_minutes'])} 分钟</b></span>
<span>库大小 <b>{data['db_size'] / 1048576:.1f} MB</b></span>
</div>
<div class="note">最近备份：{esc(data['last_backup'])}。材料缺失 {num(data['materials'][1])} 项；资料状态以各科登记为准。</div></div>
</div></div>

<div id="knowledge">__GRAPH_BLOCK__</div>

<footer>
<div>口径：学过 = LEARNING / ASSESSMENT_PENDING / CONSOLIDATE / MASTERED；考核过 = 有掌握评估记录；掌握 = MASTERED；到期复习 = PENDING/ACTIVE 且 due_date ≤ 今天。</div>
<details><summary>为什么到期复习可能是 0？</summary><div>有真实学习日期的学习记录会自动建立复习系列；未知日期不建立；独立考核结算后不重复创建。首轮通常安排在学习后第 1 天，遇到周日休息会顺延。</div></details>
<div style="margin-top:8px">静态文件供查看；服务模式支持补录学习时长 · 生成于 {esc(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</div>
</footer></div></body></html>""".replace("__GRAPH_BLOCK__", graph_block)


def knowledge_detail(root, knowledge_id):
    db = root / "database" / "studyos.sqlite3"
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT k.external_id, k.data, c.subject FROM knowledge_points k JOIN catalogs c ON c.id=k.catalog_id WHERE k.id=?", (knowledge_id,)).fetchone()
    if not row:
        return {"message": "未找到该知识点。"}
    external_id, raw, subject = row
    payload = json.loads(raw or "{}")
    source = payload.get("outline_source") or {}
    path_value = source.get("file")
    config = json.loads((root / "config" / "materials.json").read_text(encoding="utf-8-sig"))
    allowed = {item.get("path") for item in config.get("subjects", {}).get(subject, {}).get("files", {}).values() if item.get("role") == "outline"}
    if not path_value or path_value not in allowed:
        return {"message": "此知识点尚未配置可定位的教学大纲。"}
    materials_root = Path(config["root"]).resolve()
    outline = (materials_root / path_value).resolve()
    if not outline.is_relative_to(materials_root):
        return {"message": "大纲路径不在配置的资料目录内。"}
    start, end = source.get("line_start"), source.get("line_end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end - start > 300:
        return {"message": "此知识点的大纲位置未配置或无效。"}
    try:
        with outline.open(encoding="utf-8-sig") as handle:
            excerpt = "".join(itertools.islice(handle, start - 1, end))
    except OSError:
        return {"message": "配置的教学大纲文件无法读取：" + str(outline)}
    heading = re.search(r"(?m)^#{2,6}\s+" + re.escape(external_id) + r"(?:\s|$)", excerpt)
    if not heading:
        return {"message": "大纲定位与当前知识点不一致，请核对资料索引：" + str(outline)}
    section = excerpt[heading.start():]
    def field(name):
        match = re.search(r"(?m)^-\s*\*\*" + re.escape(name) + r"[：:]\*\*\s*(.*)$", section)
        return match.group(1).strip() if match else ""
    content = field("核心内容")
    return {"content": content, "goal": field("学习目标"), "message": "该大纲条目没有单独列出核心内容。" if not content else "", "source": f"来源：{path_value}（第 {start}–{end} 行；教学大纲非考试范围映射）"}


def serve(root, open_browser=True):
    sys.path.insert(0, str(root / "src"))
    from studyos.storage import Store
    from studyos.app import execute
    from studyos.common import Error
    state = {"seq": None, "page": b""}
    def refresh():
        data = collect(root)
        state["seq"] = data["event_seq"]
        state["page"] = render(data).encode("utf-8")
    refresh()
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/":
                data = collect(root)
                if data["event_seq"] != state["seq"]:
                    state["seq"] = data["event_seq"]
                    state["page"] = render(data).encode("utf-8")
                body, content_type = state["page"], "text/html; charset=utf-8"
            elif url.path == "/api/knowledge":
                knowledge_id = parse_qs(url.query).get("id", [""])[0]
                if not re.fullmatch(r"[a-fA-F0-9-]{36}", knowledge_id):
                    self.send_error(400); return
                try:
                    result = knowledge_detail(root, knowledge_id)
                except (OSError, ValueError, sqlite3.Error, KeyError):
                    result = {"message": "无法读取知识点或资料配置，请检查数据库和资料路径。"}
                body, content_type = json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
            else:
                self.send_error(404); return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            if urlsplit(self.path).path != "/api/duration":
                self.send_json(404, {"ok": False, "error": {"message": "不支持的操作"}}); return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                self.send_json(415, {"ok": False, "error": {"message": "需要 application/json"}}); return
            origin = self.headers.get("Origin")
            if origin and origin != "http://127.0.0.1:" + str(self.server.server_port):
                self.send_json(403, {"ok": False, "error": {"message": "来源校验失败"}}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 4096:
                self.send_json(400, {"ok": False, "error": {"message": "请求体为空或过大"}}); return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                session_id = payload.get("session_id")
                minutes = payload.get("minutes")
                reason = payload.get("reason") or "手动补录"
                if not isinstance(session_id, str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", session_id):
                    raise ValueError("会话 ID 无效")
                if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or not 0 < minutes <= 1440:
                    raise ValueError("分钟必须为 0 到 1440 之间的数字")
                if not isinstance(reason, str):
                    raise ValueError("补录原因无效")
                reason = reason.strip()[:120] or "手动补录"
            except (ValueError, TypeError) as error:
                self.send_json(400, {"ok": False, "error": {"message": str(error)}}); return
            db = root / "database" / "studyos.sqlite3"
            with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
                row = conn.execute("SELECT revision, status FROM learning_sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                self.send_json(404, {"ok": False, "error": {"message": "会话不存在"}}); return
            if row[1] != "COMPLETED":
                self.send_json(409, {"ok": False, "error": {"message": "只能补录已完成会话的时长"}}); return
            request = {"request_id": "ui-duration-" + uuid.uuid4().hex, "session_id": session_id,
                       "minutes": minutes, "reason": reason, "expected_revision": row[0]}
            try:
                result = execute(Store(root), "session.duration", request)
            except Error as error:
                self.send_json(400, {"ok": False, "error": {"code": error.code, "message": str(error)}}); return
            except Exception:
                self.send_json(500, {"ok": False, "error": {"message": "保存失败，请查看控制台"}}); return
            refresh()
            self.send_json(200, {"ok": True, "data": result.get("result", result)})
        def log_message(self, format, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"学习进度台：{url}（关闭此窗口结束服务）", flush=True)
    if open_browser:
        threading.Timer(.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--out", default=None)
    parser.add_argument("--serve", action="store_true", help="启动本机页面，按需读取知识点大纲")
    parser.add_argument("--no-open", action="store_true", help="启动服务时不自动打开浏览器")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.serve:
        serve(root, not args.no_open)
        return
    out = Path(args.out) if args.out else root / "reports" / "dashboard" / "index.html"
    data = collect(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    print(out)
    print(f"subjects={len(data['subjects'])} learned={sum(s['learned'] for s in data['subjects'])} "
          f"mastered={sum(s['mastered'] for s in data['subjects'])} pending={data['review_pending']} "
          f"reports={data['accepted']} report_pending={data['pending']}")


if __name__ == "__main__":
    main()
