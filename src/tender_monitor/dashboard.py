"""本地 Dashboard：直接从 SQLite 提供统计、筛选和跟进入口。"""

from __future__ import annotations

import json
import math
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .attention import clear_attention, read_attention
from .follow_up import (
    ACTIONABLE_FILTER_STATUSES,
    ACTIVE_FOLLOW_UP_STATUSES,
    COMPLETED_FOLLOW_UP_STATUSES,
    FOLLOW_UP_LABELS,
    FOLLOW_UP_STATUSES,
)
from .storage import TenderDatabase

FILTER_STATUSES = ("MATCH", "OVER_BUDGET", "REVIEW", "NO_MATCH", "FILTERED")
VERIFICATION_STATUSES = ("UNVERIFIED", "VERIFIED")
CHINA_TIMEZONE = timezone(timedelta(hours=8))

@dataclass(frozen=True)
class SetupResult:
    """首次初始化的结果。"""

    db_path: Path


def initialize_project(db_path: str | Path = "data/tenders.sqlite3") -> SetupResult:
    """创建本地目录和数据库；已有数据不会被覆盖。"""

    database_path = Path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    for directory_name in ("raw", "attachments", "evidence"):
        (database_path.parent / directory_name).mkdir(parents=True, exist_ok=True)
    with TenderDatabase(database_path):
        pass
    return SetupResult(db_path=database_path)


def _json_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _dashboard_row(row: Any) -> dict[str, Any]:
    """只暴露 Dashboard 所需字段，不把 raw_payload 放到浏览器。"""

    return {
        "id": row["id"],
        "title": row["title"] or "未命名公告",
        "source": row["source"] or "",
        "province": row["province"] or "江苏",
        "city": row["city"] or "",
        "district": row["district"] or "",
        "announcement_type": row["announcement_type"] or "",
        "procurement_method": row["procurement_method"] or "",
        "project_id": row["project_id"] or "",
        "published_at": row["published_at"] or "",
        "deadline": row["deadline"] or "",
        "budget_yuan": row["budget_yuan"],
        "budget_raw": row["budget_raw"] or "",
        "budget_status": row["budget_status"] or "UNKNOWN",
        "content": row["content"] or "",
        "url": row["url"] or "",
        "filter_status": row["filter_status"] or "",
        "filter_score": row["filter_score"] or 0,
        "filter_reasons": _json_list(row["filter_reasons_json"]),
        "filter_include_matches": _json_list(row["filter_include_matches_json"]),
        "filter_exclude_matches": _json_list(row["filter_exclude_matches_json"]),
        "verification_status": row["verification_status"] or "UNVERIFIED",
        "verified_at": row["verified_at"] or "",
        "verification_source": row["verification_source"] or "",
        "verification_notes": row["verification_notes"] or "",
        "follow_up_status": row["follow_up_status"] or "UNTRACKED",
        "follow_up_label": FOLLOW_UP_LABELS.get(row["follow_up_status"] or "UNTRACKED", "未设置"),
        "follow_up_at": row["follow_up_at"] or "",
        "follow_up_notes": row["follow_up_notes"] or "",
    }


def build_dashboard_payload(
    db: TenderDatabase,
    now: datetime | None = None,
) -> dict[str, Any]:
    """构造 Dashboard 所需的统计和公告数据。"""

    rows = [_dashboard_row(row) for row in db.list_tenders()]
    clock = now or datetime.now(tz=CHINA_TIMEZONE)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=CHINA_TIMEZONE)
    today_date = clock.astimezone(CHINA_TIMEZONE).date().isoformat()
    today_count = sum(
        str(row["published_at"] or "")[:10] == today_date for row in rows
    )
    status_counts = {status: 0 for status in FILTER_STATUSES}
    verification_counts = {status: 0 for status in VERIFICATION_STATUSES}
    follow_up_counts = {status: 0 for status in FOLLOW_UP_STATUSES}
    for row in rows:
        if row["filter_status"] in status_counts:
            status_counts[row["filter_status"]] += 1
        if row["verification_status"] in verification_counts:
            verification_counts[row["verification_status"]] += 1
        if row["follow_up_status"] in follow_up_counts:
            follow_up_counts[row["follow_up_status"]] += 1
    actionable_unverified = sum(
        row["filter_status"] in ACTIONABLE_FILTER_STATUSES
        and row["verification_status"] != "VERIFIED"
        and row["follow_up_status"] not in COMPLETED_FOLLOW_UP_STATUSES
        for row in rows
    )
    verified_actionable = sum(
        row["filter_status"] in ACTIONABLE_FILTER_STATUSES
        and row["verification_status"] == "VERIFIED"
        and row["follow_up_status"] in ACTIVE_FOLLOW_UP_STATUSES
        for row in rows
    )
    pending_verification = sum(
        row["filter_status"] in ACTIONABLE_FILTER_STATUSES
        and row["verification_status"] != "VERIFIED"
        for row in rows
    )
    verified = sum(row["verification_status"] == "VERIFIED" for row in rows)
    completed_matches = sum(
        row["filter_status"] in ACTIONABLE_FILTER_STATUSES
        and row["verification_status"] == "VERIFIED"
        and row["follow_up_status"] == "DONE"
        for row in rows
    )
    return {
        "generated_at": datetime.now(tz=CHINA_TIMEZONE).isoformat(timespec="seconds"),
        "summary": {
            "total": len(rows),
            "status_counts": status_counts,
            "verification_counts": verification_counts,
            "follow_up_counts": follow_up_counts,
            "actionable_unverified": actionable_unverified,
            "verified_actionable": verified_actionable,
            "funnel_counts": {
                "pending_verification": pending_verification,
                "verified": verified,
                "completed": completed_matches,
            },
            "today_date": today_date,
            "today_count": today_count,
            "last_published_at": next(
                (row["published_at"] for row in rows if row["published_at"]),
                "",
            ),
            "sources": sorted({row["source"] for row in rows if row["source"]}),
            "cities": sorted({row["city"] for row in rows if row["city"]}),
        },
        "tenders": rows,
    }


DASHBOARD_PAGE = r"""<!doctype html>
<html lang="zh-CN" data-theme="a">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>江苏文化采购公告 · Dashboard</title>
<style>
/* Hallmark · route: custom-03 · theme: Ferns & Fathom (tea-house system) · paper: oklch(95.5% 0.012 75) · accent: oklch(52.5% 0.16 36) · type: sans override (Archivo/PingFang) · code: mono only */
:root{
  /* type: characterful soft-serif display + literary serif body + mono data register */
  --font-display:"Archivo","PingFang SC","Helvetica Neue",ui-sans-serif,system-ui,sans-serif;
  --font-body:"Archivo","PingFang SC","Helvetica Neue",ui-sans-serif,system-ui,sans-serif;
  --font-data:"Archivo","PingFang SC","Helvetica Neue",ui-sans-serif,system-ui,sans-serif;
  --font-code:"SF Mono",ui-monospace,Menlo,monospace;
  /* paper / ink / accent — warm parchment anchor, no #fff / #000 */
  --paper:oklch(95.5% 0.012 75);    /* warm parchment      */
  --card:oklch(97% 0.010 78);       /* lighter detail face */
  --card2:oklch(92.8% 0.016 71);    /* recessed wells/hover */
  --ink:oklch(25.5% 0.018 50);      /* steeped near-black brown */
  --ink2:oklch(34% 0.020 52);
  --ink3:oklch(46% 0.020 55);
  --mute:oklch(46% 0.020 55);
  --rule:oklch(82% 0.016 70);       /* hairlines */
  --rule2:oklch(72% 0.018 68);
  --acc:oklch(52.5% 0.16 36);       /* hot steeped vermilion */
  --acc-soft:oklch(88% 0.045 40);
  --focus:oklch(48% 0.15 250);      /* cool blue ring (F&F keeps it) */
  /* status register — tea-family tints, deepened for ink-on-paper legibility */
  --st-ok:oklch(43% 0.10 152);      /* green leaf   */
  --st-warn:oklch(53% 0.115 72);    /* oolong amber */
  --st-orange:oklch(50% 0.15 42);   /* black-tea rust (over budget) */
  --st-dim:oklch(46% 0.020 55);     /* puer earth (filtered) */
  --st-mute:oklch(60% 0.018 60);
  --orange:var(--st-orange);
  /* shadows — papery tiles, one-pixel edges */
  --shadow-tile:0 1px 0 var(--rule);
  --shadow-lift:0 8px 24px -10px oklch(30% 0.03 50 / .3);
  --shadow-panel:0 1px 0 var(--rule),0 18px 48px -28px oklch(30% 0.03 50 / .28);
  --ease-out:cubic-bezier(.22,.61,.36,1);
}
[data-theme="b"]{ /* night infusion — warm dark per color.md recipe */
  --paper:oklch(21% 0.020 55); --card:oklch(25% 0.022 55); --card2:oklch(30% 0.024 55);
  --ink:oklch(92% 0.014 75); --ink2:oklch(76% 0.02 70); --ink3:oklch(62% 0.02 65); --mute:oklch(62% 0.02 65);
  --rule:oklch(34% 0.024 55); --rule2:oklch(45% 0.026 55);
  --acc:oklch(68% 0.15 40); --acc-soft:oklch(34% 0.05 40);
  --st-ok:oklch(74% 0.11 155); --st-warn:oklch(80% 0.11 75); --st-orange:oklch(76% 0.14 45); --st-dim:oklch(62% 0.02 65); --st-mute:oklch(58% 0.02 60);
  --shadow-panel:0 1px 0 var(--rule),0 18px 48px -20px oklch(0% 0 0 / .55);
}
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;overflow-x:clip}
body{background:var(--paper);color:var(--ink);font:15px/1.65 var(--font-body);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
button{font:inherit;cursor:pointer;color:inherit;background:none;border:none}
::selection{background:var(--acc-soft);color:var(--ink)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:3px}
.wrap{max-width:1480px;margin:0 auto;padding:0 42px}
/* ---- masthead: edge-aligned, tile mark ---- */
header{position:sticky;top:0;z-index:200;background:color-mix(in oklch,var(--paper) 90%,transparent);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);border-bottom:1px solid var(--rule)}
.kicker{font:10px/1.4 var(--font-data);letter-spacing:.13em;color:var(--mute);text-transform:uppercase;padding-top:14px}
.mast{display:flex;align-items:center;gap:18px;padding:11px 0 16px}
.mark{font-family:var(--font-display);font-weight:600;font-size:19px;width:38px;height:38px;display:grid;place-items:center;border:1px solid var(--rule2);border-radius:6px;background:var(--card);color:var(--ink);flex:none}
.mast h1{font-family:var(--font-display);font-size:27px;font-weight:700;letter-spacing:-.005em;margin:0;line-height:1.15}
.mast .tagline{font-size:13px;color:var(--mute);margin-top:3px}
.mast .side{margin-left:auto;display:flex;align-items:center;gap:15px;white-space:nowrap}
.snap{font:11px/1 var(--font-data);color:var(--mute)}
.snapdot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--st-ok);margin-right:7px;vertical-align:1px}
.syncstate{font:10px/1 var(--font-data);color:var(--mute);padding:4px 7px;border:1px solid var(--rule);border-radius:999px}
.syncstate.busy{color:var(--acc);border-color:var(--acc)}
.syncstate.error{color:var(--st-orange);border-color:var(--st-orange)}
.linkbtn{font-size:13px;color:var(--ink2);padding:3px 1px;border-bottom:1px solid transparent}
.linkbtn:hover{color:var(--acc);border-bottom-color:var(--acc)}
.toggle{width:32px;height:32px;border:1px solid var(--rule2);border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:14px}
.toggle:hover{border-color:var(--acc)}
.attention{border-bottom:1px solid var(--st-orange);background:var(--acc-soft);color:var(--ink2)}
.attention.hidden{display:none}
.attention .wrap{display:flex;align-items:center;gap:12px;padding-top:11px;padding-bottom:11px}
.attention .mark{width:26px;height:26px;border-color:var(--st-orange);border-radius:50%;color:var(--st-orange);font-size:14px}
.attention-copy{display:flex;flex-direction:column;gap:2px;min-width:0}
.attention-copy strong{font-size:13px}
.attention-copy span{font-size:11.5px;color:var(--ink3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.attention-meta{font:10px/1 var(--font-data);color:var(--mute);white-space:nowrap}
.attention-actions{display:flex;gap:8px;margin-left:auto;flex:none}
.attention-actions button{min-height:34px;padding:0 11px;border:1px solid var(--st-orange);border-radius:7px;font-size:12px;color:var(--st-orange);background:var(--paper)}
.attention-actions button:hover{background:var(--st-orange);color:var(--paper)}
.attention-actions .dismiss{border-color:var(--rule2);color:var(--ink2)}
/* ---- stats band ---- */
main{padding:30px 0 24px}
.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}
.stat{position:relative;min-height:114px;padding:16px 18px 15px;background:var(--card);border:1px solid var(--rule);border-radius:12px;box-shadow:var(--shadow-tile);cursor:pointer;transition-property:transform,background-color,border-color,box-shadow;transition-duration:.16s;transition-timing-function:var(--ease-out)}
.stat:hover{transform:translateY(-2px);background:var(--card2);border-color:var(--rule2);box-shadow:var(--shadow-lift)}
.stat .k{font:10px/1 var(--font-data);letter-spacing:.12em;color:var(--mute);text-transform:uppercase;display:flex;align-items:center;gap:6px;font-weight:700}
.stat .qdot{width:14px;height:14px;border:1px solid var(--rule2);border-radius:50%;color:var(--mute);font-size:8px;display:inline-flex;align-items:center;justify-content:center;cursor:help}
.stat:hover .v{color:var(--acc)}
.stat .v{font-family:var(--font-data);font-size:31px;font-weight:700;line-height:1.15;margin:9px 0 5px;font-variant-numeric:tabular-nums;letter-spacing:-.01em;color:var(--ink);transition:color .12s var(--ease-out)}
.stat.ok .v{color:var(--st-ok)} .stat.warn .v{color:var(--st-warn)} .stat.orange .v{color:var(--st-orange)}
.stat.dim .v{color:var(--st-dim)} .stat.blue .v{color:var(--ink2)}
.stat .s{font-size:11.5px;color:var(--mute)}
/* ---- sheet (the index) ---- */
.sheet{background:var(--card);border:1px solid var(--rule);border-radius:14px;box-shadow:var(--shadow-tile);margin-top:24px;overflow:hidden}
.sheet-head{padding:20px 24px 13px;border-bottom:1px solid var(--rule)}
.sec-k{font:10px/1 var(--font-data);letter-spacing:.15em;color:var(--mute);text-transform:uppercase;display:flex;align-items:center;gap:12px}
.sec-k::after{content:"";height:1px;background:var(--rule);flex:1}
.viewbar{display:flex;align-items:center;gap:7px;margin-top:16px;flex-wrap:wrap}
.viewbar .view-label{font:10px/1 var(--font-data);letter-spacing:.11em;color:var(--mute);text-transform:uppercase;margin-right:4px}
.viewbar button{min-height:34px;padding:0 11px;border:1px solid var(--rule);border-radius:7px;background:var(--paper);font-size:12px;color:var(--ink2);transition-property:background-color,border-color,color;transition-duration:.14s;transition-timing-function:var(--ease-out)}
.viewbar button:hover{border-color:var(--acc);color:var(--acc)}
.viewbar button.on{background:var(--ink);border-color:var(--ink);color:var(--paper)}
.viewbar button b{font-family:var(--font-data);font-variant-numeric:tabular-nums;margin-left:4px}
.viewbar .view-tip{margin-left:auto;color:var(--mute);font-size:11px}

.tools{display:flex;gap:12px;padding:16px 24px 18px;flex-wrap:wrap;align-items:flex-end;background:var(--card2);border-bottom:1px solid var(--rule)}
.fld{display:flex;flex-direction:column;gap:4px}
.fld label{font:10px/1 var(--font-data);letter-spacing:.11em;color:var(--mute);text-transform:uppercase}
.inp{border:1px solid var(--rule2);border-radius:8px;background:var(--paper);color:var(--ink);padding:8px 11px;font:14px/1.35 var(--font-body);outline:none;min-width:150px;min-height:40px}
.inp:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--acc-soft)}
select.inp{appearance:none;min-width:128px;cursor:pointer;background-image:linear-gradient(45deg,transparent 50%,var(--mute) 50%),linear-gradient(135deg,var(--mute) 50%,transparent 50%);background-position:calc(100% - 15px) 52%,calc(100% - 10px) 52%;background-size:5px 5px;background-repeat:no-repeat;padding-right:30px}
select.inp option{background:var(--card);color:var(--ink)}
.clr{margin-left:auto;min-height:40px;padding:0 13px;border:1px solid var(--rule2);border-radius:8px;font-size:12px;color:var(--ink2);background:var(--paper)}
.clr:hover{color:var(--acc);border-color:var(--acc)}
/* index rows */
.cols{display:grid;grid-template-columns:96px 1fr 92px 96px 104px 84px;gap:14px;padding:11px 24px;border-bottom:1px solid var(--rule2);font:9.5px/1 var(--font-data);letter-spacing:.13em;color:var(--mute);text-transform:uppercase;background:var(--card)}
.rows .row{display:grid;grid-template-columns:96px 1fr 92px 96px 104px 84px;gap:14px;align-items:center;padding:16px 24px;border-bottom:1px solid var(--rule);cursor:pointer;transition-property:background-color;transition-duration:.12s;transition-timing-function:var(--ease-out)}
.rows .row:last-child{border-bottom:none}
.rows .row:hover{background:var(--card2)}
.rows .row.dim{opacity:1}
.st{font-size:12px;font-weight:600;display:flex;align-items:center;gap:8px;white-space:nowrap}
.st::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.st.ok{color:var(--st-ok)} .st.warn{color:var(--st-warn)}
.st.settled{color:var(--st-dim)}
.st.settled::before{opacity:.7} .st.orange{color:var(--st-orange)} .st.mute{color:var(--st-mute)} .st.dim{color:var(--st-dim)}
.stcol{display:flex;flex-direction:column;gap:2px;min-width:0}
.sreason{display:none}
.t{font-family:var(--font-display);font-size:16px;font-weight:600;line-height:1.45;color:var(--ink);text-wrap:pretty}
.t .lnk{color:var(--acc);font-size:12px;margin-left:8px;opacity:0;transition:opacity .12s}
.row:hover .lnk{opacity:1}
.meta{font-size:11.5px;color:var(--mute);margin-top:3px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.otag{font:9.5px/1.6 var(--font-data);letter-spacing:.05em;padding:1px 7px;border:1px solid var(--rule2);border-radius:3px;color:var(--ink2);text-transform:uppercase}
.otag.of{border-color:var(--acc);color:var(--acc)}
.dead{color:var(--st-orange);font-weight:600}
.city{font-size:12.5px;color:var(--ink2)}
.date{font:11.5px/1 var(--font-data);color:var(--mute);font-variant-numeric:tabular-nums}
.bud{font:13.5px/1.3 var(--font-data);font-weight:700;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.bud.val{color:var(--ink)} .bud.ov{color:var(--st-orange)} .bud.unk{color:var(--mute);font-weight:400}
.bud .hint,.hint{display:block;font-size:9.5px;line-height:1.45;color:var(--mute);font-weight:400;margin-top:1px}
.futag{font:9.5px/1 var(--font-data);letter-spacing:.08em;text-transform:uppercase}

.fucell{font-size:11px;white-space:nowrap}
.fu-none{color:var(--mute);opacity:.8}
.futag.blue{color:var(--ink2);font-weight:600}.futag.vio{color:var(--st-orange);font-weight:700}.futag.ok{color:var(--st-ok);font-weight:700}.futag.mute{color:var(--st-dim)}
.pager{display:flex;align-items:center;gap:18px;padding:13px 26px;font-size:12.5px;color:var(--mute)}
.pager .mid{margin:0 auto}
.pgbtn{font-size:12.5px;color:var(--ink2);padding:4px 13px;border:1px solid var(--rule2);border-radius:5px}
.pgbtn:hover:not(:disabled){border-color:var(--acc);color:var(--acc)}
.pgbtn:disabled{opacity:.32;cursor:default}
.empty{padding:70px 20px 82px;text-align:center}
.empty .big{font:12px/1 var(--font-data);letter-spacing:.28em;color:var(--mute);margin-bottom:16px}
.empty b{font-family:var(--font-display);font-size:19px;font-weight:700}
.empty p{font-size:13px;color:var(--mute);margin:8px 0 22px}
.empty .act{color:var(--acc);font-weight:700;border-bottom:1px solid var(--acc);padding-bottom:1px;font-size:13px}
/* freq strip */
.freq{padding:14px 26px 16px;border-top:1px solid var(--rule);display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.freq .wl{font:9.5px/1 var(--font-data);letter-spacing:.13em;color:var(--mute);text-transform:uppercase;margin-right:10px}
.fch{border:1px solid var(--rule2);border-radius:99px;padding:2px 11px;font-size:11.5px;color:var(--ink2);background:var(--paper)}
.fch b{color:var(--acc);font-weight:700;margin-left:3px}
footer{padding:22px 0 64px;border-top:1px solid var(--rule);margin-top:28px;color:var(--mute);font-size:12px;line-height:1.9}
.legend{display:flex;gap:18px;flex-wrap:wrap;align-items:center;font-size:12px}
footer .note{margin-top:14px;max-width:900px}
footer code{font-family:var(--font-code);font-size:11px;background:var(--card2);padding:1px 6px;border-radius:3px}
/* dossier (assay panel) */
.backdrop{position:fixed;inset:0;background:oklch(20% 0.02 55 / .42);z-index:210;opacity:1;transition:opacity .2s var(--ease-out)}
.backdrop.hidden{opacity:0;pointer-events:none}
/* 抽屉是模态层，必须盖过 sticky 顶栏（z-index:200），否则标题会被顶栏覆盖。 */
.drawer{position:fixed;top:0;right:0;bottom:0;width:min(530px,94vw);background:var(--card);border-left:1px solid var(--rule2);z-index:220;transform:translateX(105%);transition:transform .26s var(--ease-out);display:flex;flex-direction:column;box-shadow:var(--shadow-panel)}
.drawer.open{transform:none}
.d-body{overflow-y:auto;padding:26px 30px 34px}
.d-k{font:10px/1 var(--font-data);letter-spacing:.15em;color:var(--acc);text-transform:uppercase}
.d-title{font-family:var(--font-display);font-size:20.5px;font-weight:600;line-height:1.45;margin:9px 0 12px}
.d-sub{display:flex;gap:10px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--mute);border-bottom:1px solid var(--rule);padding-bottom:18px}
.d-sec{margin:22px 0}
.d-sec .st2{font:9.5px/1 var(--font-data);letter-spacing:.15em;color:var(--mute);text-transform:uppercase;display:flex;align-items:center;gap:10px;margin-bottom:11px}
.d-sec .st2::after{content:"";height:1px;background:var(--rule);flex:1}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:0 30px}
.g2 .gi{border-bottom:1px solid var(--rule);padding:9px 0}
.g2 .gi .k{font:9px/1 var(--font-data);letter-spacing:.13em;color:var(--mute);text-transform:uppercase;margin-bottom:3px}
.g2 .gi .v{font-size:13.5px;color:var(--ink);word-break:break-all}
.g2 .gi.full{grid-column:1/-1}
.edit-prompt{margin-top:16px;border-style:solid;border-color:var(--acc);background:var(--acc-soft);color:var(--ink2)}
.edit-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 16px}
.edit-field{display:flex;flex-direction:column;gap:5px;min-width:0}
.edit-field.full{grid-column:1/-1}
.edit-field>span{font:9px/1 var(--font-data);letter-spacing:.13em;color:var(--mute);text-transform:uppercase}
.edit-field .inp{width:100%;min-width:0}
.chips{display:flex;flex-wrap:wrap;gap:7px}
.chip{font-size:12px;border:1px solid var(--rule2);border-radius:99px;padding:2px 11px;color:var(--ink2)}
.chip.hit{color:var(--st-ok);border-color:var(--st-ok)}
.chip.ex{color:var(--st-orange);border-color:var(--st-orange)}
.rlist{margin:3px 0 0;padding-left:18px;font-size:12.5px;color:var(--ink2);line-height:1.8}
.nbox{font-size:12px;color:var(--ink2);border:1px dashed var(--rule2);border-radius:6px;padding:10px 13px;line-height:1.75;background:var(--paper)}
.nbox.good{color:var(--st-ok);border-color:var(--st-ok)}
.code{position:relative;background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:10px 76px 10px 12px;font:11px/1.7 var(--font-code);color:var(--ink2);word-break:break-all;margin-top:9px}
.copy{position:absolute;right:7px;top:7px;font-size:10.5px;border:1px solid var(--rule2);border-radius:4px;padding:2px 10px;color:var(--ink2)}
.copy:hover{color:var(--acc);border-color:var(--acc)}
.fu-seg{display:flex;gap:8px;flex-wrap:wrap}
.fu-seg button{font-size:12.5px;padding:7px 15px;border:1px solid var(--rule2);border-radius:5px;color:var(--ink2)}
.fu-seg button:hover{border-color:var(--acc);color:var(--acc)}
.fu-seg button.on{background:var(--ink);color:var(--paper);border-color:var(--ink);font-weight:700}
textarea.inp{width:100%;min-height:62px;border:1px solid var(--rule2);border-radius:6px;background:var(--paper);padding:8px 11px;font:13px/1.5 var(--font-body);resize:vertical}
textarea.inp:focus{border-color:var(--acc)}
.d-acts{display:flex;gap:10px;margin-top:16px}
.d-acts .ghost{flex:1;border:1px solid var(--rule2);border-radius:6px;padding:10px 16px;color:var(--ink2);font-weight:600}
.d-acts .ghost:hover{border-color:var(--acc);color:var(--acc)}
.d-acts .primary{flex:1;border:1px solid var(--ink);border-radius:6px;padding:10px 16px;background:var(--ink);color:var(--paper);font-weight:700}
.d-acts .primary:hover{border-color:var(--acc);background:var(--acc)}
.d-acts .primary:disabled{opacity:.55;cursor:wait}
.d-foot{font-size:10.5px;color:var(--mute);border-top:1px dashed var(--rule2);padding-top:10px;margin-top:16px;line-height:1.7}
.toast-wrap{position:fixed;bottom:26px;left:50%;transform:translateX(-50%);z-index:99}
.toast{background:var(--ink);color:var(--paper);padding:10px 20px;border-radius:99px;font-size:13px;box-shadow:var(--shadow-panel);animation:tin .16s var(--ease-out)}
@keyframes tin{from{opacity:0;transform:translateY(6px)}to{opacity:1}}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
@media(max-width:1080px){.stats{grid-template-columns:repeat(3,1fr);row-gap:24px}.stat+.stat{border-left:none;padding-left:0}.cols,.rows .row{grid-template-columns:84px 1fr 104px}.cols .c3,.row .city,.cols .c4,.row .date{display:none}.cols{display:none}}
@media(max-width:640px){.stats{grid-template-columns:repeat(2,1fr)}.mast .tagline{display:none}.wrap{padding:0 18px}}

</style>
</head>
<body>
<header><div class="wrap">
  <div class="kicker">JIANGSU CULTURE PROCUREMENT MONITOR · 每日扫描 摄影 / 文旅宣传 / 公共文化</div>
  <div class="mast">
    <h1>江苏文化采购公告</h1>
    <span class="tagline">值得看的机会，今天先给你。</span>
    <div class="side">
      <span><span class="snapdot"></span>数据更新于 <span id="snapTime">加载中…</span></span>
      <span class="syncstate" id="syncState" title="每 60 秒检查一次本地 Dashboard 数据；不会增加外部采集请求">自动同步准备中</span>
      <button class="linkbtn" id="btnReload">⟳ 刷新数据</button>
      <button class="toggle" id="btnTheme" title="夜墨模式">🌙</button>
    </div>
  </div>
</div></header>

<section class="attention hidden" id="attention" aria-live="polite">
  <div class="wrap">
    <div class="mark">!</div>
    <div class="attention-copy">
      <strong id="attentionTitle">需要人工处理</strong>
      <span id="attentionMessage"></span>
    </div>
    <span class="attention-meta" id="attentionMeta"></span>
    <div class="attention-actions">
      <button id="attentionOpen">打开官方查询</button>
      <button class="dismiss" id="attentionClear">标记已处理</button>
    </div>
  </div>
</section>

<main class="wrap">
  <section class="stats" id="stats"></section>

  <section class="sheet">
    <div class="sheet-head">
      <div class="sec-k">公告清单 TENDER LIST</div>
      <div class="viewbar" id="viewbar">
        <span class="view-label">快速视图</span>
        <button data-view="match">命中目标 <b>0</b></button>
        <span class="view-tip">点击公告行查看详情；其余阶段请点击上方漏斗卡片</span>
      </div>
      </div>
    <div class="tools">
      <div class="fld" style="flex:1 1 200px"><label>检索</label><input class="inp" id="q" placeholder="标题 / 项目编号 / 关键词"></div>
      <div class="fld"><label>状态</label><select class="inp" id="fStatus"></select></div>
      <div class="fld"><label>来源</label><select class="inp" id="fSrc"></select></div>
      <div class="fld"><label>类型</label><select class="inp" id="fType"></select></div>
      <div class="fld"><label>地市</label><select class="inp" id="fCity"></select></div>
      <div class="fld"><label>核验</label><select class="inp" id="fVerify"></select></div>
      <div class="fld"><label>跟进</label><select class="inp" id="fFu"></select></div>
      <div class="fld"><label>排序</label><select class="inp" id="fSort"><option value="pub">最新发布</option><option value="id">最新入库</option><option value="bud">预算从高到低</option><option value="title">标题</option></select></div>
      <button class="clr" id="btnClear">清空筛选</button>
    </div>
    <div class="cols"><span>状态</span><span>公告</span><span class="c3">地市</span><span class="c4">发布</span><span class="c5" style="text-align:right">预算</span><span class="c6">跟进</span></div>
    <div class="rows" id="rows"></div>
    <div id="emptyBox"></div>
    <div class="pager" id="pager"></div>
    <div class="freq" id="freq"></div>
  </section>
</main>

<footer class="wrap">
  <div class="legend" id="legend"></div>
  <div class="note"><b>数据边界</b>：公告均为公开招标信息。官方源（江苏政府采购网）需人工验证码半自动采集；江苏 / 泰兴招标网为聚合发现源。预算提示值来自聚合站列表页、未经锚定字段确认，仅作线索；公告详情可在抽屉中补充并保存，外部原文仍需打开查看。</div>
</footer>

<div class="backdrop hidden" id="backdrop"></div>
<aside class="drawer" id="drawer"><div class="d-body">
  <div class="d-k" id="dK"></div>
  <div class="d-title" id="dTitle"></div>
  <div class="d-sub" id="dMeta"></div>
  <div id="dBody"></div>
  <div class="d-acts">
    <button class="ghost" id="openUrl">打开原文 ↗</button>
    <button class="ghost" id="dClose">关闭</button>
  </div>
</div></aside>
<div class="toast-wrap" id="toastWrap"></div>

<script>

"use strict";
const $=id=>document.getElementById(id);
let RAW=[],rows=[],todayDate='',dashboardSummary={};
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SRC={ccgp_jiangsu:{n:'江苏政府采购网',t:'官方'},okcis_jiangsu:{n:'江苏招标网',t:'聚合'},okcis_taixing:{n:'泰兴招标网',t:'聚合'}};
const TYPE={gkzb:'公开招标',jzcs:'竞争性磋商',zbgg:'中标公告',cgyx:'采购意向',htgg:'合同公告',fbxy:'框架协议征集',bn:'招标公告','招标公告':'招标公告'};
const ST={MATCH:{l:'命中目标',c:'ok'},REVIEW:{l:'预算待确认',c:'warn'},OVER_BUDGET:{l:'预算需确认',c:'orange'},NO_MATCH:{l:'未命中',c:'mute'},FILTERED:{l:'未命中',c:'mute'},UNMATCHED:{l:'未命中',c:'mute'}};
const STATUS_FILTERS=[
  {value:'MATCH',label:'命中目标'},
  {value:'REVIEW',label:'预算待确认'},
  {value:'OVER_BUDGET',label:'预算需确认'},
  {value:'UNMATCHED',label:'未命中'},
];
const UNMATCHED_STATUSES=['NO_MATCH','FILTERED'];
const VER={VERIFIED:{l:'已核验',c:'ok'},UNVERIFIED:{l:'未核验',c:'mute'}};
const FU={PENDING:{l:'待跟进',c:'blue'},IN_PROGRESS:{l:'跟进中',c:'vio'},DONE:{l:'已完成',c:'ok'},NOT_REQUIRED:{l:'暂不跟进',c:'mute'}};
const FU_ORDER=['PENDING','IN_PROGRESS','NOT_REQUIRED','DONE'];
const ACTIONABLE_STATUSES=['MATCH','OVER_BUDGET','REVIEW'];
const isActionable=r=>ACTIONABLE_STATUSES.includes(r.filter_status);
const isPendingVerification=r=>isActionable(r)&&r.verification_status!=='VERIFIED';
const isVerified=r=>r.verification_status==='VERIFIED';
const isConfirmedTarget=r=>isVerified(r)&&isActionable(r)&&r.follow_up_status!=='NOT_REQUIRED';
const isCompletedMatch=r=>isVerified(r)&&isActionable(r)&&r.follow_up_status==='DONE';
function displayStatus(r){
  const s=ST[r.filter_status]||{l:r.filter_status,c:'mute'};
  if(r.verification_status==='VERIFIED'&&r.follow_up_status==='NOT_REQUIRED')return {l:'未命中目标',c:'orange'};
  if(r.verification_status!=='VERIFIED'&&r.filter_status==='MATCH')return {l:'疑似命中',c:'warn'};
  if(r.verification_status!=='VERIFIED'&&r.filter_status==='REVIEW')return {l:'待核验',c:'warn'};
  return s;
}

const needsFollowUp=r=>isActionable(r)&&!['DONE','NOT_REQUIRED'].includes(r.follow_up_status)&&(r.verification_status!=='VERIFIED'||['PENDING','IN_PROGRESS'].includes(r.follow_up_status));
let counts={};
function compute(){
  counts={
    total:rows.length,
    today:Number(dashboardSummary.today_count)||0,
    pendingVerification:rows.filter(isPendingVerification).length,
    verified:rows.filter(isVerified).length,
    confirmedTarget:rows.filter(isConfirmedTarget).length,
    completedMatches:rows.filter(isCompletedMatch).length,
    todo:rows.filter(needsFollowUp).length,
    unmatched:rows.filter(r=>UNMATCHED_STATUSES.includes(r.filter_status)).length,
    official:rows.filter(r=>r.source==='ccgp_jiangsu').length,
  };
}

const theme=localStorage.getItem('jt-dash-v2-theme')||'a';
document.documentElement.dataset.theme=theme;
const setTheme=t=>{document.documentElement.dataset.theme=t;localStorage.setItem('jt-dash-v2-theme',t);
  $('btnTheme').textContent=t==='a'?'🌙':'☀️';$('btnTheme').title=t==='a'?'切到夜墨':'切到纸白'};
$('btnTheme').onclick=()=>setTheme(document.documentElement.dataset.theme==='a'?'b':'a');
setTheme(theme);


const toast=m=>{const t=document.createElement('div');t.className='toast';t.textContent=m;$('toastWrap').appendChild(t);setTimeout(()=>t.remove(),2600)};

const st={view:null,q:'',status:'',src:'',type:'',city:'',verify:'',fu:'',sort:'pub',page:1};
const preset=v=>{st.view=v==='all'?null:v;st.page=1};

function applyFilter(list){
  const f=st;
  if(f.view==='todo')list=list.filter(needsFollowUp);
  else if(f.view==='today')list=list.filter(r=>(r.published_at||'').slice(0,10)===todayDate);
  else if(f.view==='pending')list=list.filter(isPendingVerification);
  else if(f.view==='match'||f.view==='confirmed')list=list.filter(isConfirmedTarget);
  else if(f.view==='verified')list=list.filter(isVerified);
  else if(f.view==='completed')list=list.filter(isCompletedMatch);
  else if(f.view==='confirm')list=list.filter(r=>['REVIEW','OVER_BUDGET'].includes(r.filter_status));
  else if(f.view==='filtered'||f.view==='unmatched')list=list.filter(r=>UNMATCHED_STATUSES.includes(r.filter_status));
  if(f.status==='UNMATCHED')list=list.filter(r=>UNMATCHED_STATUSES.includes(r.filter_status));
  else if(f.status)list=list.filter(r=>r.filter_status===f.status);
  if(f.src)list=list.filter(r=>r.source===f.src);
  if(f.type)list=list.filter(r=>(TYPE[r.announcement_type]||r.announcement_type||'未分类')===f.type);
  if(f.city)list=list.filter(r=>r.city===f.city);
  if(f.verify)list=list.filter(r=>r.verification_status===f.verify);
  if(f.fu)list=list.filter(r=>r.follow_up_status===f.fu);
  if(f.q){const q=f.q.toLowerCase();list=list.filter(r=>(r.title||'').toLowerCase().includes(q)||(r.project_id||'').toLowerCase().includes(q)||(r.url||'').toLowerCase().includes(q));}
  return list;
}
function sortRows(list){
  if(st.sort==='pub')return list.slice().sort((a,b)=>(b.published_at||'').localeCompare(a.published_at||''));
  if(st.sort==='id')return list.slice().sort((a,b)=>Number(b.id)-Number(a.id));
  if(st.sort==='bud')return list.slice().sort((a,b)=>(Number(b.budget_yuan)||-1)-(Number(a.budget_yuan)||-1));
  return list.slice().sort((a,b)=>(a.title||'').localeCompare(b.title||'','zh'));
}
const PAGE=50;
function budHtml(r){
  const y=r.budget_status==='NORMAL'||r.budget_status==='OVER_BUDGET'?Number(r.budget_yuan):null;
  if(y&&y>0)return '<span class="bud '+(r.budget_status==='OVER_BUDGET'?'ov':'val')+'">'+(y>=10000?(y/10000).toFixed(2)+' 万':'¥'+y.toLocaleString('zh-CN'))+'</span>';
  const title=r.budget_raw?'列表预算线索：'+r.budget_raw+'，需打开原文核验':'预算未公开';
  return '<span class="bud unk" title="'+esc(title)+'">未公开</span>';
}
function rowHtml(r){
  const s=displayStatus(r);
  const machineStatus=ST[r.filter_status]||{l:r.filter_status,c:'mute'};
  const ex=(r.filter_exclude_matches||[]).filter(w=>!w.startsWith('公告类型'));
  const dim=UNMATCHED_STATUSES.includes(r.filter_status)?'dim':'';
  const fu=r.follow_up_status&&r.follow_up_status!=='UNTRACKED'?FU[r.follow_up_status]:null;
  const closed=r.follow_up_status==='DONE'||r.follow_up_status==='NOT_REQUIRED';
  const srcm=SRC[r.source]||{n:r.source,t:''};
  const meta=[];
  if(srcm.t)meta.push('<span class="otag '+(srcm.t==='官方'?'of':'')+'">'+srcm.t+'</span>');
  meta.push('<span>'+esc(srcm.n)+'</span>');
  meta.push('<span class="sp">·</span><span>'+esc(TYPE[r.announcement_type]||r.announcement_type||'未分类')+'</span>');
  if(r.project_id)meta.push('<span class="sp">·</span><span>'+esc(r.project_id)+'</span>');
  if(r.deadline)meta.push('<span class="sp">·</span><span class="dead">截止 '+esc(r.deadline)+'</span>');
  /* 列1=机器筛选状态（恒定）；跟进状态放独立列。已收尾的行状态转灰并保留提示，不再琥珀刺眼 */
  const fuCls=fu?({PENDING:'blue',IN_PROGRESS:'vio',DONE:'ok',NOT_REQUIRED:'mute'})[r.follow_up_status]||'mute':'';
  const stCell = '<div class="stcol"><span class="st '+(closed&&r.follow_up_status==='DONE'?'settled':s.c)+'"'+(closed?' title="'+esc('已选择「'+fu.l+'」；机器原判：'+machineStatus.l+(ex.length?'（'+ex.join('、')+'）':''))+'"':(ex.length?' title="'+esc('排除原因：'+ex.join('、'))+'"':''))+'>'+s.l+'</span>'+(closed?'':(ex.length?'<span class="sreason" title="'+esc('排除原因：'+ex.join('、'))+'">'+esc(ex.join('、'))+'</span>':''))+'</div>';
  const fuCell = '<div class="fucell">'+(fu?'<span class="futag '+fuCls+'" title="跟进状态">'+fu.l+'</span>':'<span class="fu-none">—</span>')+'</div>';
  return '<div class="row '+dim+'" data-url="'+esc(r.url)+'">'
   +stCell
   +'<div><div class="t">'+esc(r.title)+'<span class="lnk">详情 ›</span></div><div class="meta">'+meta.join(' ')+'</div></div>'
   +'<div class="city">'+esc(r.city||'—')+'</div>'
   +'<div class="date">'+esc((r.published_at||'').slice(0,10))+'</div>'
   +'<div style="text-align:right">'+budHtml(r)+'</div>'
   +fuCell
   +'</div>';
}
function render(){
  const list=applyFilter(rows);
  const total=list.length;
  const pages=Math.max(1,Math.ceil(total/PAGE));
  if(st.page>pages)st.page=pages;
  const slice=sortRows(list).slice((st.page-1)*PAGE,st.page*PAGE);
  $('rows').innerHTML=slice.map(rowHtml).join('');
  $('emptyBox').innerHTML=slice.length?'':'<div class="empty"><div class="big">—— 暂无 ——</div><b>'+(st.view==='todo'?'待办已清空 🎉':'没有符合条件的公告')+'</b><p>'+(st.view==='todo'?'今天没有需要处理的采购机会。':'试试清空筛选条件，或切换上方视图。')+'</p>'+(st.view==='todo'?'<button class="act" data-go="all">查看全部公告 →</button>':'<button class="act" id="emptyClear">清空筛选 →</button>')+'</div>';
  const ec=$('emptyClear');if(ec)ec.onclick=clearAll;
  const go=$('rows')&&document.querySelector('[data-go="all"]');if(go)go.onclick=()=>{preset('all');render()};
  $('pager').innerHTML='<button class="pgbtn" id="pgPrev">‹ 上一页</button><span class="mid">第 '+st.page+' / '+pages+' 页 · 共 '+total+' 条 · 每页 '+PAGE+' 条</span><button class="pgbtn" id="pgNext">下一页 ›</button>';
  const pv=$('pgPrev'),nx=$('pgNext');
  pv.disabled=st.page<=1;nx.disabled=st.page>=pages;
  pv.onclick=()=>{st.page--;render()};nx.onclick=()=>{st.page++;render()};
  renderStats();renderViewbar();
}
function statHtml(cl,k,val,sub,tip,view,extra){
  return '<div class="stat '+cl+'" data-view="'+view+'" data-extra="'+(extra||'')+'" title="">'
   +'<div class="k">'+k+'<span class="qdot" title="'+esc(tip)+'">?</span></div>'
   +'<div class="v">'+val+'</div><div class="s">'+sub+'</div></div>';
}
function renderStats(){
  const C=[
   ['blue','公告总数',counts.total,'官方 '+counts.official+' · 聚合 '+(counts.total-counts.official),'全部已采集公告。','all',''],
   ['blue','今日新增',counts.today,'发布于 '+todayDate,'今天新出现的公告。','today',''],
   ['warn','待核验',counts.pendingVerification,'疑似目标 · 等待人工确认','机器筛出、尚未人工核验的候选。','pending',''],
   ['ok','已核验',counts.verified,'包含暂不跟进项目','已完成人工核验的公告。','verified',''],
   ['ok','已完成',counts.completedMatches,'已核验 · 跟进完成','已核验且跟进状态为“已完成”的公告。','completed',''],
  ];
  $('stats').innerHTML=C.map(c=>statHtml(...c)).join('');
  $('stats').querySelectorAll('.stat').forEach(el=>el.onclick=()=>{
    const v=el.dataset.view,ex=el.dataset.extra;
    preset(v);st.status='';st.verify=ex==='verified'?'VERIFIED':'';st.fu='';
    $('rows').closest('.sheet').scrollIntoView({behavior:'smooth',block:'start'});
    syncSel();render();
  });
}
function renderViewbar(){
  const values={match:counts.confirmedTarget};
  const current=st.view||'all';
  $('viewbar').querySelectorAll('button[data-view]').forEach(btn=>{
    const view=btn.dataset.view;
    btn.classList.toggle('on',view===current);
    const count=btn.querySelector('b');
    if(count)count.textContent=values[view]||0;
    btn.onclick=()=>{preset(view);syncSel();render()};
  });
}
function syncSel(){$('fStatus').value=st.status;$('fSrc').value=st.src;$('fType').value=st.type;$('fCity').value=st.city;$('fVerify').value=st.verify;$('fFu').value=st.fu;$('fSort').value=st.sort;$('q').value=st.q}
function clearAll(){$('q').value='';st.q='';st.status=st.src=st.type=st.city=st.verify=st.fu='';render()}
function fillSelects(){
  ['fStatus','fSrc','fType','fCity','fVerify','fFu'].forEach(id=>$(id).innerHTML='');
  const uniq=fn=>[...new Set(rows.map(fn).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh'));
  ['fStatus','fSrc','fType','fCity','fVerify','fFu'].forEach(id=>{
    const o=document.createElement('option');o.value='';o.textContent='全部';$(id).appendChild(o);
  });
  STATUS_FILTERS.forEach(({value,label})=>{const o=document.createElement('option');o.value=value;o.textContent=label;$('fStatus').appendChild(o)});
  uniq(r=>r.source).forEach(k=>{const m=SRC[k]||{n:k,t:''};const o=document.createElement('option');o.value=k;o.textContent=(m.t?'['+m.t+'] ':'')+m.n;$('fSrc').appendChild(o)});
  uniq(r=>TYPE[r.announcement_type]||r.announcement_type).forEach(t=>{const o=document.createElement('option');o.value=t;o.textContent=t;$('fType').appendChild(o)});
  uniq(r=>r.city).forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;$('fCity').appendChild(o)});
  Object.keys(VER).forEach(k=>{const o=document.createElement('option');o.value=k;o.textContent=VER[k].l;$('fVerify').appendChild(o)});
  Object.keys(FU).forEach(k=>{if(k==='UNTRACKED')return;const o=document.createElement('option');o.value=k;o.textContent=FU[k].l;$('fFu').appendChild(o)});
}
/* ---- 详情 ---- */
function openDetail(url){
  const r=RAW.find(x=>x.url===url);if(!r)return;
  const s=displayStatus(r);
  $('dK').textContent=(s.l)+' · '+(r.verification_status==='VERIFIED'?'已核验':'未核验');
  $('dTitle').textContent=r.title;
  const srcm=SRC[r.source]||{n:r.source};
  $('dMeta').innerHTML='<span class="otag '+(srcm.t==='官方'?'of':'')+'">'+srcm.t+'</span><span>'+esc(srcm.n)+'</span><span>'+esc(TYPE[r.announcement_type]||r.announcement_type||'—')+'</span><span>'+esc(r.city||'江苏')+'</span><span>发布 '+esc(r.published_at||'—')+'</span>';
  const hitChips=(r.filter_include_matches||[]).map(w=>'<span class="chip hit">'+esc(w)+'</span>').join('');
  const exChips=(r.filter_exclude_matches||[]).map(w=>'<span class="chip ex">'+esc(w)+'</span>').join('');
  const reasons=(r.filter_reasons||[]).map(x=>'<li>'+esc(x)+'</li>').join('');
  const editPrompt=r.verification_status==='VERIFIED'?'':'<div class="nbox edit-prompt">首次核验请先核对并补全项目编号、预算、截止时间和正文摘要；保存后此提示会消失。</div>';
  $('dBody').innerHTML=
   '<div class="d-sec"><div class="st2">公告信息</div><div class="g2">'
   +'<div class="gi"><div class="k">来源</div><div class="v">'+esc(srcm.n)+'</div></div>'
   +'<div class="gi"><div class="k">公告类型</div><div class="v">'+esc(TYPE[r.announcement_type]||r.announcement_type||'—')+'</div></div>'
   +'<div class="gi"><div class="k">采购方式</div><div class="v">'+esc(r.procurement_method||'—')+'</div></div>'
   +'<div class="gi"><div class="k">地区</div><div class="v">'+esc(r.city||'江苏')+'</div></div>'
   +'<div class="gi"><div class="k">发布日期</div><div class="v">'+esc(r.published_at||'—')+'</div></div>'
   +'</div></div>'
   +editPrompt
   +'<div class="d-sec"><div class="st2">公告详情</div>'
   +'<div class="edit-grid">'
   +'<label class="edit-field"><span>项目编号</span><input class="inp" id="editProjectId" value="'+esc(r.project_id||'')+'" placeholder="待确认"></label>'
   +'<label class="edit-field"><span>预算金额（元）</span><input class="inp" id="editBudgetYuan" inputmode="decimal" value="'+(r.budget_yuan==null?'':esc(String(r.budget_yuan)))+'" placeholder="例如 9000"></label>'
   +'<label class="edit-field"><span>预算原文</span><input class="inp" id="editBudgetRaw" value="'+esc(r.budget_raw||'')+'" placeholder="例如 ¥9,000"></label>'
   +'<label class="edit-field"><span>截止时间</span><input class="inp" id="editDeadline" value="'+esc(r.deadline||'')+'" placeholder="YYYY-MM-DD HH:MM"></label>'
   +'<label class="edit-field full"><span>正文摘要</span><textarea class="inp" id="editContent" placeholder="粘贴或整理公告正文摘要">'+esc(r.content||'')+'</textarea></label>'
   +'<label class="edit-field full"><span>核验备注</span><textarea class="inp" id="editNotes" placeholder="记录判断依据、联系人或证据说明">'+esc(r.verification_notes||'')+'</textarea></label>'
   +'</div>'
   +'<div class="d-acts"><button class="primary" id="saveDetail">保存公告详情</button></div>'
   +'</div>'
   +(reasons?'<div class="d-sec"><div class="st2">机器筛选结论</div><ul class="rlist">'+reasons+'</ul></div>':'')
   +((hitChips||exChips)?'<div class="d-sec"><div class="st2">关键词命中</div><div class="chips">'+(hitChips||'<span style="font-size:12px;color:var(--ink3)">无命中词</span>')+'</div>'+(exChips?'<div class="chips" style="margin-top:8px">'+exChips+'</div>':'')+'</div>':'')
   +'<div class="d-sec"><div class="st2">核验</div>'
   +(r.verification_status==='VERIFIED'
     ?'<div class="nbox good">✓ 已核验'+(r.verified_at?' · '+esc(String(r.verified_at).slice(0,16)):'')+(r.verification_source?' · 来源 '+esc(r.verification_source):'')+'</div>'
       +(r.verification_notes?'<div class="nbox" style="margin-top:8px">'+esc(r.verification_notes)+'</div>':'')
     :'<div class="nbox">请在上方“公告详情”中核对字段并保存，保存后会标记为“已核验”。</div>')
   +'</div>'
   +'<div class="d-sec"><div class="st2">跟进</div>'
   +'<div class="fu-seg" id="fuSeg">'+FU_ORDER.map(s2=>'<button data-fu="'+s2+'" class="'+(r.follow_up_status===s2?'on':'')+'">'+FU[s2].l+'</button>').join('')+'</div>'
   +'<textarea class="inp" id="fuNotes" placeholder="跟进备注：报价 / 联系人 / 截止提醒…">'+esc(r.follow_up_notes||'')+'</textarea>'
   +(r.follow_up_status&&r.follow_up_status!=='UNTRACKED'?'<div style="font-size:11px;color:var(--ink3);margin-top:7px">最近跟进：'+(r.follow_up_at||'—')+'</div>':'')
   +'<div class="d-acts"><button class="primary" id="saveFu">保存</button></div>'
   +'</div>'
   +'<div class="d-foot">公告详情和跟进状态会写入本地 SQLite；外部原文仍需点击“打开原文”查看。</div>';
  $('saveDetail').onclick=async()=>{
    const budgetText=$('editBudgetYuan').value.trim();
    const budgetYuan=budgetText===''?null:Number(budgetText);
    if(budgetText!==''&&(!Number.isFinite(budgetYuan)||budgetYuan<0)){toast('预算金额需填写非负数字');return}
    const btn=$('saveDetail');btn.disabled=true;btn.textContent='保存中…';
    try{
      const res=await fetch('/api/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        url:r.url,
        project_id:$('editProjectId').value.trim()||null,
        budget_yuan:budgetYuan,
        budget_raw:$('editBudgetRaw').value.trim(),
        deadline:$('editDeadline').value.trim(),
        content:$('editContent').value,
        notes:$('editNotes').value,
        verification_source:'dashboard_manual'
      })});
      const j=await res.json().catch(()=>({}));
      if(!res.ok)throw new Error(j.error||('HTTP '+res.status));
      toast('公告详情已保存');closeDetail();await loadData();
    }catch(err){toast('保存失败：'+err.message);btn.disabled=false;btn.textContent='保存公告详情'}
  };
  const seg=$('fuSeg');
  seg.querySelectorAll('button').forEach(b=>b.onclick=()=>{seg.querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on')});
  $('saveFu').onclick=async()=>{
    const on=[...seg.querySelectorAll('button')].find(b=>b.classList.contains('on'));
    const status=on?on.dataset.fu:'PENDING';
    const btn=$('saveFu');btn.disabled=true;btn.textContent='保存中…';
    try{
      const res=await fetch('/api/follow-up',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:r.url,status:status,notes:$('fuNotes').value})});
      const j=await res.json().catch(()=>({}));
      if(!res.ok)throw new Error(j.error||('HTTP '+res.status));
      if(status==='DONE')preset('completed');
      toast(status==='DONE'?'已保存，已移到已完成':'已保存');closeDetail();await loadData();
    }catch(err){toast('保存失败：'+err.message);btn.disabled=false;btn.textContent='保存'}
  };
  $('openUrl').onclick=()=>window.open(r.url,'_blank');
  $('dClose').onclick=closeDetail;
  $('backdrop').onclick=closeDetail;
  document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=()=>{
    const t=document.createElement('textarea');t.value=b.dataset.copy;document.body.appendChild(t);t.select();
    try{document.execCommand('copy')}catch(e){}
    t.remove();toast('命令已复制');
  });
  $('drawer').classList.add('open');$('backdrop').classList.remove('hidden');
}
function closeDetail(){$('drawer').classList.remove('open');$('backdrop').classList.add('hidden')}
$('rows').addEventListener('click',e=>{
  const row=e.target.closest('.row[data-url]');if(!row)return;
  openDetail(row.dataset.url);
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDetail()});

function renderFreq(){
  const inc={};rows.forEach(r=>(r.filter_include_matches||[]).forEach(w=>inc[w]=(inc[w]||0)+1));
  const ex={};rows.forEach(r=>(r.filter_exclude_matches||[]).forEach(w=>{if(!w.startsWith('公告类型'))ex[w]=(ex[w]||0)+1}));
  const incTop=Object.entries(inc).sort((a,b)=>b[1]-a[1]).slice(0,8);
  const exTop=Object.entries(ex).sort((a,b)=>b[1]-a[1]).slice(0,5);
  $('freq').innerHTML='<span class="wl">命中词频</span>'+(incTop.map(([w,c])=>'<span class="fch">'+esc(w)+'<b>×'+c+'</b></span>').join('')||'<span style="color:var(--ink3);font-size:11.5px">暂无命中</span>')
   +'<span class="wl" style="margin-left:auto">排除词频</span>'+exTop.map(([w,c])=>'<span class="fch">'+esc(w)+'<b>×'+c+'</b></span>').join('');
}
function renderLegend(){
  $('legend').innerHTML='<span style="color:var(--ink3);font-weight:800;letter-spacing:.14em">状态</span>'
   +STATUS_FILTERS.map(({value,label})=>'<span class="st '+ST[value].c+'">'+label+'</span>').join('')
   +'<span style="color:var(--ink3);font-weight:800;letter-spacing:.14em;margin-left:10px">跟进</span>'
   +'<span class="futag blue">待跟进</span><span class="futag vio">跟进中</span><span class="futag ok">已完成</span><span class="futag mute">暂不跟进</span>';
}
['fStatus','fSrc','fType','fCity','fVerify','fFu'].forEach(id=>$(id).addEventListener('change',e=>{st[id.slice(1)]=e.target.value;st.page=1;render()}));
$('fSort').addEventListener('change',e=>{st.sort=e.target.value;render()});
$('q').addEventListener('input',e=>{st.q=e.target.value;st.page=1;render()});
$('btnClear').onclick=clearAll;
let firstLoaded=false;
let loading=false;
const AUTO_SYNC_MS=60*1000;
function setSyncState(text,kind=''){
  const el=$('syncState');
  if(!el)return;
  el.textContent=text;
  el.className='syncstate'+(kind?' '+kind:'');
}
async function loadData(){
  if(loading)return;
  loading=true;
  setSyncState('同步中…','busy');
  try{
    const res=await fetch('/api/dashboard',{cache:'no-store'});
    if(!res.ok)throw new Error('HTTP '+res.status);
    const P=await res.json();
    RAW=P.tenders;rows=RAW;dashboardSummary=P.summary||{};todayDate=dashboardSummary.today_date||'';compute();
    $('snapTime').textContent=P.generated_at;
    if(!firstLoaded){
      const q=new URLSearchParams(location.hash.slice(1));
      const hview=q.get('view');
      if(hview&&['todo','today','pending','match','confirmed','verified','completed','confirm','filtered','unmatched','all'].includes(hview))preset(hview);
      else if(counts.todo>0)preset('todo');
      if(q.get('theme'))setTheme(q.get('theme'));
      firstLoaded=true;
    }
    fillSelects();syncSel();render();renderFreq();renderLegend();
    setSyncState('自动同步 · '+String(P.generated_at||'').slice(11,19));
  }catch(err){
    setSyncState(firstLoaded?'同步失败 · 保留上次数据':'同步失败 · 将重试','error');
    toast('加载失败：'+err.message);
    if(!firstLoaded){
      $('rows').innerHTML='<div class="empty"><div class="big">—— 无法连接 ——</div><b>本地 Dashboard 服务未响应</b><p>请先运行 tender-monitor dashboard，再刷新本页。</p></div>';
      $('pager').innerHTML='';$('stats').innerHTML='';
      if(err instanceof TypeError&&err.message.includes('fetch'))$('emptyBox').innerHTML='';
    }
  }finally{
    loading=false;
  }
}
const ATTENTION_TITLES={CAPTCHA_REQUIRED:'需要人工验证码',RATE_LIMIT:'采集被限流保护',MANUAL_ACTION_REQUIRED:'需要人工处理',AUTOMATION_FAILED:'自动采集需要处理'};
function renderAttention(event){
  const bar=$('attention');
  if(!event){bar.classList.add('hidden');return}
  $('attentionTitle').textContent=ATTENTION_TITLES[event.kind]||'需要人工处理';
  $('attentionMessage').textContent=event.message||'请打开官方查询页面完成处理。';
  $('attentionMeta').textContent=(event.source||'')+(event.created_at?' · '+String(event.created_at).slice(0,16):'');
  $('attentionOpen').onclick=()=>window.open(event.url||'http://www.ccgp-jiangsu.gov.cn/jiangsu/cggg_search.html','_blank');
  bar.classList.remove('hidden');
}
async function loadAttention(){
  try{
    const res=await fetch('/api/attention',{cache:'no-store'});
    if(!res.ok)throw new Error('HTTP '+res.status);
    const payload=await res.json();
    renderAttention(payload.attention||null);
  }catch(err){
    /* 提示接口不可用时不遮挡主列表，下一次轮询继续尝试。 */
  }
}
$('attentionClear').onclick=async()=>{
  const btn=$('attentionClear');btn.disabled=true;
  try{
    const res=await fetch('/api/attention/clear',{method:'POST'});
    if(!res.ok)throw new Error('HTTP '+res.status);
    renderAttention(null);
  }catch(err){toast('标记失败：'+err.message);btn.disabled=false}
};
$('btnReload').onclick=loadData;
loadData();
loadAttention();
setInterval(()=>{
  if(document.visibilityState==='visible'){loadData();loadAttention()}
},AUTO_SYNC_MS);
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='visible'){loadData();loadAttention()}
});

</script>
</body></html>
"""


def render_dashboard_html() -> str:
    """返回不依赖 CDN 的单页 Dashboard（Ferns & Fathom 视觉重设计版）。"""

    return DASHBOARD_PAGE


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _request_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0 or content_length > 64 * 1024:
        raise ValueError("请求内容不能为空且不能超过 64 KB")
    try:
        payload = json.loads(handler.rfile.read(content_length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("请求必须是有效的 JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError("请求 JSON 必须是对象")
    return payload


def make_dashboard_handler(
    db_path: str | Path,
    *,
    allow_writes: bool = True,
) -> type[BaseHTTPRequestHandler]:
    """为指定数据库创建无状态 HTTP handler。"""

    database_path = Path(db_path)
    attention_path = database_path.parent / "attention.json"

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                body = render_dashboard_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if route == "/api/dashboard":
                with TenderDatabase(database_path) as db:
                    _json_response(self, build_dashboard_payload(db))
                return
            if route == "/api/attention":
                _json_response(self, {"attention": read_attention(attention_path)})
                return
            _json_response(self, {"error": "not found"}, status=404)

        def do_POST(self) -> None:
            route = urlparse(self.path).path
            if route not in ("/api/follow-up", "/api/verify", "/api/attention/clear"):
                _json_response(self, {"error": "not found"}, status=404)
                return
            if not allow_writes:
                _json_response(self, {"error": "当前 Dashboard 为只读模式"}, status=403)
                return
            if route == "/api/attention/clear":
                clear_attention(attention_path)
                _json_response(self, {"status": "CLEARED"})
                return
            try:
                payload = _request_json(self)
                url = payload.get("url")
                if route == "/api/verify":
                    if not isinstance(url, str) or not url.strip():
                        raise TypeError("url 必须是字符串")
                    budget_value = payload.get("budget_yuan")
                    if budget_value in (None, ""):
                        budget_yuan = None
                    elif isinstance(budget_value, bool):
                        raise TypeError("budget_yuan 必须是数字")
                    else:
                        try:
                            budget_yuan = float(budget_value)
                        except (TypeError, ValueError) as exc:
                            raise TypeError("budget_yuan 必须是数字") from exc
                        if not math.isfinite(budget_yuan) or budget_yuan < 0:
                            raise ValueError("budget_yuan 必须是非负有限数字")

                    def optional_text(name: str) -> str | None:
                        value = payload.get(name)
                        if value is None:
                            return None
                        if not isinstance(value, str):
                            raise TypeError(f"{name} 必须是字符串")
                        return value

                    source = optional_text("verification_source") or "dashboard_manual"
                    with TenderDatabase(database_path) as db:
                        tender_id = db.verify_tender(
                            url,
                            project_id=optional_text("project_id"),
                            budget_yuan=budget_yuan,
                            budget_raw=optional_text("budget_raw"),
                            deadline=optional_text("deadline"),
                            content=optional_text("content"),
                            verification_source=source,
                            notes=optional_text("notes"),
                        )
                    _json_response(self, {"id": tender_id, "status": "VERIFIED"})
                    return
                status = payload.get("status")
                notes = payload.get("notes")
                if not isinstance(url, str) or not isinstance(status, str):
                    raise TypeError("url 和 status 必须是字符串")
                if notes is not None and not isinstance(notes, str):
                    raise TypeError("notes 必须是字符串")
                with TenderDatabase(database_path) as db:
                    tender_id = db.update_follow_up(url, status=status, notes=notes)
                _json_response(self, {"id": tender_id, "status": status.strip().upper()})
            except KeyError as exc:
                _json_response(self, {"error": str(exc)}, status=404)
            except (TypeError, ValueError) as exc:
                _json_response(self, {"error": str(exc)}, status=400)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return DashboardHandler


def run_dashboard(
    db_path: str | Path = "data/tenders.sqlite3",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    allow_write: bool | None = None,
) -> None:
    """启动本地 Dashboard；默认仅监听本机。"""

    initialize_project(db_path)
    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    writable = is_loopback if allow_write is None else allow_write
    server = ThreadingHTTPServer(
        (host, port),
        make_dashboard_handler(db_path, allow_writes=writable),
    )
    if not writable:
        print("Dashboard 已启动为只读模式；如需写入跟进状态，请仅绑定回环地址。")
    print(f"Dashboard: http://{host}:{port}/")
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
