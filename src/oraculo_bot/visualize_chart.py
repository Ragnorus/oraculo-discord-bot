from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import PlayerStats

# Composite score weights — inspired by PIR (Performance Index Rating) in MOBA analytics research.
# Additive by design so the score accumulates naturally over time for the race.
_WIN_W = 20.0
_KILL_W = 2.0
_ASSIST_W = 1.2
_DEATH_W = 1.5
_DMG_DIV = 4000.0
_GOLD_DIV = 1500.0
_CS_DIV = 12.0

# Checkpoint counts per period key
_CHECKPOINTS: dict[str, int] = {"weekly": 7, "monthly": 8, "yearly": 12}


def performance_score(stats: "PlayerStats") -> float:
    return (
        stats.wins * _WIN_W
        + stats.kills * _KILL_W
        + stats.assists * _ASSIST_W
        - stats.deaths * _DEATH_W
        + stats.total_damage / _DMG_DIV
        + stats.gold_earned / _GOLD_DIV
        + stats.minions_killed / _CS_DIV
    )


def compute_checkpoints(start: datetime, end: datetime, period_key: str) -> list[datetime]:
    n = _CHECKPOINTS.get(period_key, 7)
    delta = (end - start) / n
    return [start + delta * i for i in range(1, n + 1)]


def build_race_payload(
    player_scores: dict[str, list[tuple[datetime, float]]],
    title: str,
    queue_label: str,
    period_label: str,
) -> dict:
    all_dates: list[datetime] = sorted(
        {dt for scores in player_scores.values() for dt, _ in scores}
    )
    frames = []
    for dt in all_dates:
        values = [
            {"name": name, "value": round(next((v for d, v in reversed(scores) if d <= dt), 0.0), 2)}
            for name, scores in player_scores.items()
        ]
        values.sort(key=lambda x: x["value"], reverse=True)
        frames.append({"date": dt.strftime("%Y-%m-%d"), "values": values})
    return {"title": title, "queue": queue_label, "period": period_label, "frames": frames}


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oráculo — Performance Race</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0e1a;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;
  display:flex;flex-direction:column;align-items:center;padding:24px 16px;min-height:100vh}
h1{font-size:1.35rem;font-weight:700;color:#c89b3c;letter-spacing:.02em;margin-bottom:4px}
.sub{font-size:.82rem;color:#718096;margin-bottom:20px}
#wrap{width:100%;max-width:900px}
svg{width:100%;height:auto;overflow:visible}
.bar-rect{rx:4px}
.lbl-name{font-size:13px;fill:#e2e8f0;dominant-baseline:middle;text-anchor:end}
.lbl-val{font-size:12px;fill:#a0aec0;dominant-baseline:middle}
.lbl-rank{font-size:10px;fill:#4a5568;dominant-baseline:middle;text-anchor:middle}
.date-lbl{font-size:44px;font-weight:900;fill:#1e2d40;text-anchor:end;dominant-baseline:auto}
.x-axis .tick text{fill:#4a5568;font-size:11px}
.x-axis .tick line{stroke:#1e2d40}
.x-axis .domain{display:none}
.controls{margin-top:14px;display:flex;gap:10px;align-items:center}
button{background:#1e2d40;border:1px solid #2d3f55;color:#e2e8f0;padding:7px 18px;
  border-radius:6px;cursor:pointer;font-size:.88rem;transition:background .15s}
button:hover{background:#2d3f55}
.prog{color:#4a5568;font-size:.82rem}
.score-info{margin-top:18px;max-width:900px;width:100%;
  background:#0e1623;border:1px solid #1e2d40;border-radius:8px;padding:12px 16px}
.score-info summary{cursor:pointer;color:#718096;font-size:.82rem;user-select:none}
.score-info p{margin-top:8px;font-size:.78rem;color:#718096;line-height:1.6}
.score-info code{background:#1e2d40;border-radius:3px;padding:1px 5px;color:#c89b3c;font-size:.82rem}
</style>
</head>
<body>
<h1 id="title"></h1>
<div class="sub" id="sub"></div>
<div id="wrap"><svg id="chart"></svg></div>
<div class="controls">
  <button id="btn">⏸ Pause</button>
  <span class="prog" id="prog"></span>
</div>
<details class="score-info">
  <summary>ℹ️ How is the Performance Score calculated?</summary>
  <p>
    <code>Score = (Wins × 20) + (Kills × 2.0 + Assists × 1.2 − Deaths × 1.5)
    + (Damage ÷ 4000) + (Gold ÷ 1500) + (CS ÷ 12)</code><br><br>
    <strong>Wins × 20:</strong> winning is the most important factor, so each win adds 20 points.<br>
    <strong>Kills × 2.0:</strong> rewards kills. <strong>Assists × 1.2:</strong> rewards team participation at a lower weight.<br>
    <strong>Deaths × 1.5:</strong> subtracts points so the score does not reward risky kills alone.<br>
    <strong>Damage ÷ 4000:</strong> 40,000 damage contributes 10 points.<br>
    <strong>Gold ÷ 1500:</strong> 15,000 gold contributes 10 points.<br>
    <strong>CS ÷ 12:</strong> 120 CS contributes 10 points.<br><br>
    The divisors keep large raw values such as damage and gold from overwhelming wins and KDA.
    The score is <strong>cumulative</strong> — it grows as players complete matches, making the
    race meaningful throughout the period. These weights are a heuristic for this leaderboard,
    not an official Riot rating or scientifically validated skill measure. Because it is cumulative,
    players with more games may score higher.
  </p>
</details>
<script>
const data = __RACE_DATA__;

document.getElementById("title").textContent = data.title;
document.getElementById("sub").textContent = data.queue + " · " + data.period;

const DURATION = 800;
const MAX_BARS = 10;
const BAR_H = 40;
const PAD = 0.12;

const frames = data.frames;
const allNames = [...new Set(frames.flatMap(f => f.values.map(v => v.name)))];
const nBars = Math.min(MAX_BARS, allNames.length);
const innerH = nBars * BAR_H;

const margin = {top: 16, right: 80, bottom: 44, left: 10};
const svgW = 880;
const svgH = innerH + margin.top + margin.bottom + 56;
const W = svgW - margin.left - margin.right;

const colorScale = d3.scaleOrdinal(d3.schemeTableau10).domain(allNames);

const xScale = d3.scaleLinear().range([0, W]);
const yScale = d3.scaleBand().domain(d3.range(nBars)).range([0, innerH]).paddingInner(PAD);
const BH = yScale.bandwidth();
const NAME_X = margin.left - 8;

const svg = d3.select("#chart").attr("viewBox", `0 0 ${svgW} ${svgH}`);
const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

const xAxisG = g.append("g").attr("class","x-axis").attr("transform",`translate(0,${innerH+4})`);

const dateLbl = g.append("text")
  .attr("class","date-lbl")
  .attr("x", W - 4).attr("y", innerH - 8);

const barsG = g.append("g");

function ranked(frame) {
  return [...frame.values].sort((a,b) => b.value - a.value).slice(0, nBars);
}

function update(frame, dur) {
  const rv = ranked(frame);
  const maxVal = rv[0]?.value || 1;
  xScale.domain([0, maxVal * 1.08]);

  const t = g.transition().duration(dur).ease(d3.easeLinear);

  xAxisG.transition(t).call(
    d3.axisBottom(xScale).ticks(5).tickSize(-innerH - 4)
      .tickFormat(d => d >= 1000 ? (d/1000).toFixed(1)+"k" : d.toFixed(0))
  );

  dateLbl.transition(t).text(frame.date);

  // ── bars ──
  const bars = barsG.selectAll("g.bi").data(rv, d => d.name);

  const enter = bars.enter().append("g").attr("class","bi")
    .attr("transform", (d,i) => `translate(0,${innerH + 10})`).style("opacity",0);

  enter.append("rect").attr("class","bar-rect")
    .attr("height", BH).attr("rx", 4).attr("width", 0);

  enter.append("text").attr("class","lbl-name")
    .attr("x", NAME_X).attr("y", BH/2);

  enter.append("text").attr("class","lbl-val")
    .attr("y", BH/2);

  enter.append("text").attr("class","lbl-rank")
    .attr("x", -NAME_X > 0 ? NAME_X/2 : -12).attr("y", BH/2);

  const merged = enter.merge(bars);

  merged.transition(t)
    .attr("transform", (d,i) => `translate(0,${yScale(i)})`).style("opacity",1);

  merged.select("rect").transition(t)
    .attr("width", d => Math.max(0, xScale(d.value)))
    .attr("fill", d => colorScale(d.name));

  merged.select(".lbl-name").transition(t).text(d => d.name);

  merged.select(".lbl-val").transition(t)
    .attr("x", d => Math.max(0, xScale(d.value)) + 6)
    .text(d => d.value.toFixed(1));

  merged.select(".lbl-rank").transition(t)
    .text((d, i) => "#" + (i + 1));

  bars.exit().transition(t)
    .attr("transform", `translate(0,${innerH + 10})`).style("opacity",0).remove();
}

let idx = 0, playing = true, timer = null;

function advance() {
  if (idx >= frames.length) {
    playing = false;
    document.getElementById("btn").textContent = "▶ Replay";
    return;
  }
  update(frames[idx], DURATION);
  document.getElementById("prog").textContent = (idx + 1) + " / " + frames.length;
  idx++;
  if (playing && idx < frames.length) {
    timer = setTimeout(advance, DURATION + 60);
  } else if (idx >= frames.length) {
    playing = false;
    document.getElementById("btn").textContent = "▶ Replay";
  }
}

document.getElementById("btn").addEventListener("click", () => {
  if (!playing && idx >= frames.length) {
    idx = 0; playing = true;
    document.getElementById("btn").textContent = "⏸ Pause";
    advance();
  } else if (playing) {
    playing = false; if (timer) clearTimeout(timer);
    document.getElementById("btn").textContent = "▶ Play";
  } else {
    playing = true;
    document.getElementById("btn").textContent = "⏸ Pause";
    advance();
  }
});

if (frames.length > 0) {
  update(frames[0], 0);
  document.getElementById("prog").textContent = "1 / " + frames.length;
  idx = 1;
  if (frames.length > 1) timer = setTimeout(advance, 1400);
}
</script>
</body>
</html>"""


def generate_chart_html(payload: dict) -> str:
    json_data = json.dumps(payload, ensure_ascii=False)
    return _HTML_TEMPLATE.replace("__RACE_DATA__", json_data)
