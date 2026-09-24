const STAGE_ORDER = [
  "ingest", "read", "extract", "validate",
  "match_vendor", "match_po", "duplicates", "decide",
];

const STAGE_LABELS = {
  ingest: "Ingest",
  read: "Read",
  extract: "Extract",
  validate: "Validate",
  match_vendor: "Match vendor",
  match_po: "Match PO",
  duplicates: "Duplicates",
  decide: "Decide",
};

// RuleResult has no stage field (CLAUDE.md defines it exactly) — group by rule_id prefix,
// used both for live SSE events (which already know their stage) and REST replay.
function stageForRuleId(ruleId) {
  if (ruleId.startsWith("VEN")) return "match_vendor";
  if (ruleId === "V-05" || ruleId.startsWith("PO")) return "match_po";
  if (ruleId.startsWith("DUP")) return "duplicates";
  if (ruleId.startsWith("V-")) return "validate";
  return "other";
}

function emptyStages() {
  const stages = {};
  for (const s of STAGE_ORDER) stages[s] = { status: "pending", ms: null, data: null };
  return stages;
}

async function resetDemo() {
  if (!confirm("This deletes all saved runs so the sample invoices can be re-run cleanly. Vendor/PO/ledger data is untouched. Continue?")) {
    return;
  }
  const res = await fetch("/api/reset", { method: "POST" });
  if (!res.ok) {
    alert("Reset failed. Please try again.");
    return;
  }
  window.location.href = "/";
}

const INR_FORMATTER = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatMoney(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return INR_FORMATTER.format(n);
}

function formatQty(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return n % 1 === 0 ? String(n) : n.toFixed(2);
}

function formatMs(ms) {
  if (ms === null || ms === undefined) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function formatRunTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const now = new Date();
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const time = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (sameDay(d, now)) return `Today, ${time}`;
  if (sameDay(d, yesterday)) return `Yesterday, ${time}`;
  return `${d.toLocaleDateString("en-IN", { day: "numeric", month: "short" })}, ${time}`;
}

const OUTCOME_LABELS = { APPROVE: "Approved", NEEDS_REVIEW: "Needs review", REJECT: "Rejected" };

function outcomeLabel(code) {
  return OUTCOME_LABELS[code] || code || "—";
}

function badgeClass(status) {
  switch (status) {
    case "pass":
    case "APPROVE":
      return "bg-emerald-50 text-emerald-700 border border-emerald-200";
    case "warn":
    case "NEEDS_REVIEW":
      return "bg-amber-50 text-amber-700 border border-amber-200";
    case "fail":
    case "REJECT":
      return "bg-rose-50 text-rose-700 border border-rose-200";
    case "skip":
      return "bg-slate-100 text-slate-500 border border-slate-200";
    default:
      return "bg-slate-100 text-slate-500 border border-slate-200";
  }
}

function stepDotClass(status) {
  switch (status) {
    case "running":
    case "done":
      return "bg-brand";
    case "failed":
      return "bg-rose-500";
    default:
      return "bg-slate-200";
  }
}

const SAMPLE_META = [
  { file: "01_happy_acme.pdf", label: "Happy path · Acme", tag: "Happy path" },
  { file: "02_split_po_brightline.pdf", label: "Split PO · Brightline", tag: "Split PO" },
  { file: "03_duplicate_northwind.pdf", label: "Duplicate · Northwind", tag: "Duplicate" },
  { file: "04_scanned_no_po_sahyadri.pdf", label: "Scanned, no PO · Sahyadri", tag: "Scanned, no PO" },
  { file: "05_price_variance_metro.pdf", label: "Price variance · Metro", tag: "Price variance" },
  { file: "06_blocked_quantum.pdf", label: "Blocked vendor · Quantum", tag: "Blocked vendor" },
];

const RULE_STAGES = ["validate", "match_vendor", "match_po", "duplicates"];
const STATUS_RANK = { fail: 0, warn: 1, pass: 2, skip: 3 };

document.addEventListener("alpine:init", () => {
  Alpine.data("runView", () => {
    let chartInstance = null;

    return {
      stageOrder: STAGE_ORDER,
      stageLabels: STAGE_LABELS,
      sampleMeta: SAMPLE_META,
      dragOver: false,
      uploading: false,
      runId: null,
      stages: emptyStages(),
      invoiceData: null,
      fieldsMissing: [],
      ruleResults: [],
      lineMatches: [],
      decision: null,
      error: null,
      eventSource: null,
      checksSummary: "",

      init() {
        const params = new URLSearchParams(window.location.search);
        const runId = params.get("run_id");
        if (runId) this.loadFromRun(runId);
      },

      stepDotClass(status) { return stepDotClass(status); },
      badgeClass(status) { return badgeClass(status); },
      formatMoney(v) { return formatMoney(v); },
      formatQty(v) { return formatQty(v); },
      formatMs(ms) { return formatMs(ms); },

      reset() {
        this.runId = null;
        this.stages = emptyStages();
        this.invoiceData = null;
        this.fieldsMissing = [];
        this.ruleResults = [];
        this.lineMatches = [];
        this.decision = null;
        this.error = null;
        this.checksSummary = "";
        if (this.eventSource) { this.eventSource.close(); this.eventSource = null; }
        if (chartInstance) { chartInstance.destroy(); chartInstance = null; }
      },

      processAnother() {
        this.reset();
        window.history.replaceState({}, "", "/");
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
      },

      resultsForStage(stage) {
        return this.ruleResults.filter((r) => stageForRuleId(r.rule_id) === stage);
      },

      sortedResultsForStage(stage) {
        return [...this.resultsForStage(stage)].sort(
          (a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status]
        );
      },

      stageProgress(stage) {
        const results = this.resultsForStage(stage).filter((r) => r.status !== "skip");
        const total = results.length;
        const pass = results.filter((r) => r.status === "pass").length;
        let color = "bg-emerald-500";
        if (results.some((r) => r.status === "fail")) color = "bg-rose-500";
        else if (results.some((r) => r.status === "warn")) color = "bg-amber-500";
        return { pct: total ? Math.round((pass / total) * 100) : 0, color, pass, total };
      },

      // Visible stepper label — kept short (~18 chars) so nothing truncates or overlaps the
      // next stage; stepNoteFull() below has the complete text, shown as a title tooltip.
      stepNoteShort(stage) {
        const s = this.stages[stage];
        if (!s || s.status === "pending") return "";
        if (s.status === "running") return "Working…";
        if (s.status === "failed") return "Failed";
        const data = s.data || {};
        if (!Object.keys(data).length) return ""; // replayed run: no per-stage data was persisted
        switch (stage) {
          case "ingest":
            return data.seen_before ? "Seen before" : "New file";
          case "read":
            return data.mode === "text" ? "Text layer" : "Scanned";
          case "extract": {
            const total = 9;
            const missing = (data.fields_missing || []).length;
            const found = Math.max(total - missing, 0);
            return data.cached ? `${found}/${total} (cached)` : `${found}/${total} fields`;
          }
          case "validate":
          case "duplicates":
            return this._resultsNoteShort(data.results);
          case "match_vendor":
            if (!data.vendor_name) return "Not found";
            return `${data.vendor_name.split(/\s+/)[0]} ✓`;
          case "match_po":
            if (!data.po_number) return "No PO";
            return data.explicit_or_inferred === "inferred" ? `${data.po_number} ?` : data.po_number;
          case "decide":
            return data.outcome || "";
          default:
            return "";
        }
      },

      _resultsNoteShort(results) {
        if (!results || !results.length) return "";
        const fails = results.filter((r) => r.status === "fail").length;
        const warns = results.filter((r) => r.status === "warn").length;
        if (fails) return `${fails} failed`;
        if (warns) return `${warns} flagged`;
        return "All passed";
      },

      // Full descriptive text for the stepper note's title tooltip.
      stepNoteFull(stage) {
        const s = this.stages[stage];
        if (!s || s.status === "pending") return "";
        if (s.status === "running") return "Working…";
        if (s.status === "failed") return (s.data && s.data.error) || "Failed";
        const data = s.data || {};
        if (!Object.keys(data).length) return ""; // replayed run: no per-stage data was persisted
        switch (stage) {
          case "ingest":
            return data.seen_before ? "File seen before" : "New file";
          case "read":
            if (data.mode === "text") {
              const pages = data.page_count || 0;
              return `Text extracted, ${pages} page${pages === 1 ? "" : "s"}`;
            }
            return "Scanned — using vision path";
          case "extract": {
            const total = 9;
            const missing = (data.fields_missing || []).length;
            const found = Math.max(total - missing, 0);
            return data.cached ? `${found}/${total} fields found (cached)` : `${found}/${total} fields found`;
          }
          case "validate":
          case "duplicates":
            return this._resultsNoteFull(data.results);
          case "match_vendor":
            return data.vendor_name ? `Matched ${data.vendor_name} (${data.match_score ?? "—"})` : "Vendor not identified";
          case "match_po":
            if (!data.po_number) return "No PO identified";
            return data.explicit_or_inferred === "inferred" ? `Inferred ${data.po_number}` : `Matched ${data.po_number}`;
          case "decide":
            return data.outcome || "";
          default:
            return "";
        }
      },

      _resultsNoteFull(results) {
        if (!results || !results.length) return "";
        const fails = results.filter((r) => r.status === "fail").length;
        const warns = results.filter((r) => r.status === "warn").length;
        if (fails) return `${fails} failed`;
        if (warns) return `${warns} flagged`;
        return "All checks passed";
      },

      // Finds the earlier run a REJECT-driving duplicate rule matched against, so the result
      // header can link straight to it (DUP-01 is always internal; DUP-02 only when it matched
      // a prior run rather than the ledger).
      duplicateLinkRunId() {
        const dup01 = this.ruleResults.find((r) => r.rule_id === "DUP-01" && r.status === "fail");
        if (dup01 && dup01.evidence && dup01.evidence.original_run_id) {
          return dup01.evidence.original_run_id;
        }
        const dup02 = this.ruleResults.find((r) => r.rule_id === "DUP-02" && r.status === "fail");
        if (dup02 && dup02.evidence && dup02.evidence.source === "run" && dup02.evidence.original_run_id) {
          return dup02.evidence.original_run_id;
        }
        return null;
      },

      lineStatusInfo(line) {
        if (!line.matched) return { label: "No PO match", cls: badgeClass("fail") };
        if (line.price_status === "fail" || line.qty_status === "fail") {
          return { label: "Mismatch", cls: badgeClass("fail") };
        }
        return { label: "OK", cls: badgeClass("pass") };
      },

      qtyDelta(line) {
        if (line.qty_status !== "fail") return null;
        const invoiceQty = Number(line.invoice_qty);
        const remaining = Number(line.po_remaining_before);
        if (Number.isNaN(invoiceQty) || Number.isNaN(remaining)) return null;
        const over = invoiceQty - remaining;
        return over > 0 ? `${formatQty(over)} over PO balance` : null;
      },

      priceDelta(line) {
        if (line.price_status !== "fail") return null;
        const invoicePrice = Number(line.invoice_pre_tax_unit_price);
        const poPrice = Number(line.po_unit_price);
        if (!poPrice || Number.isNaN(invoicePrice)) return null;
        const pct = ((invoicePrice - poPrice) / poPrice) * 100;
        const sign = pct >= 0 ? "+" : "-";
        return `${sign}${Math.abs(pct).toFixed(0)}% ${pct >= 0 ? "over" : "under"} PO`;
      },

      checkCounts() {
        const relevant = this.ruleResults.filter((r) => r.status !== "skip");
        const pass = relevant.filter((r) => r.status === "pass").length;
        const warn = relevant.filter((r) => r.status === "warn").length;
        const fail = relevant.filter((r) => r.status === "fail").length;
        return { pass, warn, fail, total: relevant.length };
      },

      updateChecksChart() {
        const counts = this.checkCounts();
        this.checksSummary = counts.total ? `${counts.pass} of ${counts.total} passed` : "";
        const ctx = this.$refs.checksChart;
        if (!ctx || typeof Chart === "undefined") return;
        if (!chartInstance) {
          chartInstance = new Chart(ctx, {
            type: "doughnut",
            data: {
              labels: ["Pass", "Warn", "Fail"],
              datasets: [{
                data: [counts.pass, counts.warn, counts.fail],
                backgroundColor: ["#10b981", "#f59e0b", "#f43f5e"],
                borderWidth: 0,
              }],
            },
            options: {
              cutout: "70%",
              plugins: { legend: { display: false } },
              animation: { duration: 300 },
            },
          });
        } else {
          chartInstance.data.datasets[0].data = [counts.pass, counts.warn, counts.fail];
          chartInstance.update();
        }
      },

      async handleDrop(e) {
        this.dragOver = false;
        const file = e.dataTransfer.files[0];
        if (file) await this.startRun(file);
      },

      async handleFileInput(e) {
        const file = e.target.files[0];
        if (file) await this.startRun(file);
      },

      async loadSample(name) {
        const res = await fetch(`/samples/${name}`);
        const blob = await res.blob();
        const file = new File([blob], name, { type: "application/pdf" });
        await this.startRun(file);
      },

      async startRun(file) {
        this.reset();
        this.uploading = true;
        try {
          const formData = new FormData();
          formData.append("file", file);
          const res = await fetch("/runs", { method: "POST", body: formData });
          if (!res.ok) throw new Error(`Upload failed (${res.status})`);
          const { run_id } = await res.json();
          this.runId = run_id;
          window.history.replaceState({}, "", `/?run_id=${run_id}`);
          this.attachStream(run_id);
        } catch (e) {
          this.error = e.message;
        } finally {
          this.uploading = false;
        }
      },

      attachStream(runId) {
        const es = new EventSource(`/runs/${runId}/stream`);
        this.eventSource = es;
        es.onmessage = (e) => {
          const evt = JSON.parse(e.data);
          this.applyStageEvent(evt);
          if (evt.stage === "saved" || evt.status === "failed") {
            es.close();
          }
        };
        es.onerror = () => {
          this.error = this.error || "Connection to server lost.";
          es.close();
        };
      },

      applyStageEvent(evt) {
        if (evt.stage === "saved") return;

        if (this.stages[evt.stage]) {
          this.stages[evt.stage] = { status: evt.status, ms: evt.ms, data: evt.data };
        }
        if (evt.status === "failed") {
          this.error = evt.data?.error || "Run failed.";
          return;
        }
        if (evt.status !== "done") return;

        const data = evt.data || {};
        if (evt.stage === "extract") {
          this.invoiceData = data.invoice || null;
          this.fieldsMissing = data.fields_missing || [];
        } else if (RULE_STAGES.includes(evt.stage)) {
          this.ruleResults = [...this.ruleResults, ...(data.results || [])];
          if (evt.stage === "match_po") {
            this.lineMatches = data.line_matches || [];
          }
          this.updateChecksChart();
        } else if (evt.stage === "decide") {
          this.decision = {
            outcome: data.outcome,
            reasons: data.reasons || [],
            summary: data.summary,
            next_action: data.next_action,
          };
        }
      },

      async loadFromRun(runId) {
        this.reset();
        this.runId = runId;
        try {
          const res = await fetch(`/api/runs/${runId}`);
          if (res.status === 404) {
            // Stale/invalid run id (e.g. after a demo reset) — fail silently back to the
            // empty upload state instead of showing a stuck "Run not found" error.
            this.reset();
            window.history.replaceState({}, "", "/");
            return;
          }
          if (!res.ok) throw new Error("Run not found");
          const run = await res.json();
          for (const stage of STAGE_ORDER) {
            this.stages[stage] = { status: run.status === "failed" ? "failed" : "done", ms: null, data: null };
          }
          this.invoiceData = run.invoice;
          this.fieldsMissing = [];
          this.ruleResults = run.rule_results || [];
          this.lineMatches = run.line_matches || [];
          this.decision = run.decision;
          if (run.status === "failed") this.error = run.error;
          this.updateChecksChart();
        } catch (e) {
          this.error = e.message;
        }
      },
    };
  });

  Alpine.data("dashboardView", () => {
    let chartInstance = null;

    return {
      runs: [],
      loading: true,
      outcomeCounts: { APPROVE: 0, NEEDS_REVIEW: 0, REJECT: 0 },
      kpis: { invoicesProcessed: 0, approvedValue: 0, autoApprovedPct: 0, avgProcessingMs: 0 },
      kpiDisplay: { invoicesProcessed: "0", approvedValue: "₹0.00", autoApprovedPct: "0%", avgProcessingMs: "0ms" },
      attentionRuns: [],
      topIssues: [],
      detailsLoading: true,

      async init() {
        const res = await fetch("/api/runs");
        this.runs = await res.json();
        this.computeCounts();
        this.computeKpis();
        this.loading = false;
        this.animateKpis();
        this.$nextTick(() => this.renderOutcomeChart());
        await this.fetchAttentionDetails();
      },

      computeCounts() {
        const counts = { APPROVE: 0, NEEDS_REVIEW: 0, REJECT: 0 };
        for (const run of this.runs) {
          if (run.outcome && counts[run.outcome] !== undefined) counts[run.outcome]++;
        }
        this.outcomeCounts = counts;
      },

      computeKpis() {
        const total = this.runs.length;
        const approvedValue = this.runs
          .filter((r) => r.outcome === "APPROVE")
          .reduce((sum, r) => sum + (Number(r.total) || 0), 0);
        const autoApprovedPct = total ? Math.round((this.outcomeCounts.APPROVE / total) * 100) : 0;
        const avgProcessingMs = total
          ? Math.round(this.runs.reduce((sum, r) => sum + (r.duration_ms || 0), 0) / total)
          : 0;
        this.kpis = { invoicesProcessed: total, approvedValue, autoApprovedPct, avgProcessingMs };
      },

      // Only NEEDS_REVIEW/REJECT runs can ever carry a fail/warn rule result (APPROVE means
      // zero fail/warn by decide.py's logic), so detail fetches are limited to those.
      async fetchAttentionDetails() {
        const targets = this.runs.filter((r) => r.outcome === "NEEDS_REVIEW" || r.outcome === "REJECT");
        const details = (
          await Promise.all(
            targets.map((r) =>
              fetch(`/api/runs/${r.run_id}`).then((res) => (res.ok ? res.json() : null))
            )
          )
        ).filter(Boolean);

        this.attentionRuns = details
          .map((d) => ({
            run_id: d.run_id,
            vendor_name: d.vendor_name,
            invoice_number: d.invoice_number,
            outcome: d.outcome,
            created_at: d.created_at,
            reason: d.decision?.summary || "No summary available.",
          }))
          .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

        this.topIssues = this.aggregateTopIssues(details);
        this.detailsLoading = false;
      },

      aggregateTopIssues(details) {
        const tally = new Map();
        for (const d of details) {
          for (const r of d.rule_results || []) {
            if (r.status !== "fail") continue;
            const entry = tally.get(r.rule_id) || { rule_id: r.rule_id, name: r.name, count: 0 };
            entry.count += 1;
            tally.set(r.rule_id, entry);
          }
        }
        const issues = [...tally.values()].sort((a, b) => b.count - a.count).slice(0, 5);
        const max = issues.length ? issues[0].count : 0;
        return issues.map((i) => ({ ...i, pct: max ? Math.round((i.count / max) * 100) : 0 }));
      },

      renderOutcomeChart() {
        const counts = this.outcomeCounts;
        const ctx = this.$refs.outcomeChart;
        if (!ctx || typeof Chart === "undefined") return;
        const data = [counts.APPROVE, counts.NEEDS_REVIEW, counts.REJECT];
        if (!chartInstance) {
          chartInstance = new Chart(ctx, {
            type: "doughnut",
            data: {
              labels: ["Approved", "Needs review", "Rejected"],
              datasets: [{ data, backgroundColor: ["#10b981", "#f59e0b", "#f43f5e"], borderWidth: 0 }],
            },
            options: {
              cutout: "70%",
              plugins: { legend: { display: false } },
              animation: { duration: 300 },
            },
          });
        } else {
          chartInstance.data.datasets[0].data = data;
          chartInstance.update();
        }
      },

      outcomeLegend() {
        const total = this.runs.length;
        return ["APPROVE", "NEEDS_REVIEW", "REJECT"].map((key) => {
          const count = this.outcomeCounts[key];
          return {
            key,
            label: outcomeLabel(key),
            count,
            pct: total ? Math.round((count / total) * 100) : 0,
            dotClass: { APPROVE: "bg-emerald-500", NEEDS_REVIEW: "bg-amber-500", REJECT: "bg-rose-500" }[key],
            barClass: { APPROVE: "bg-emerald-500", NEEDS_REVIEW: "bg-amber-500", REJECT: "bg-rose-500" }[key],
          };
        });
      },

      animateValue(key, target, formatter, duration = 700) {
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reduceMotion) {
          this.kpiDisplay[key] = formatter(target);
          return;
        }
        const start = performance.now();
        const from = 0;
        const step = (now) => {
          const t = Math.min((now - start) / duration, 1);
          const eased = 1 - Math.pow(1 - t, 2);
          this.kpiDisplay[key] = formatter(from + (target - from) * eased);
          if (t < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      },

      animateKpis() {
        this.animateValue("invoicesProcessed", this.kpis.invoicesProcessed, (v) => String(Math.round(v)));
        this.animateValue("approvedValue", this.kpis.approvedValue, (v) => formatMoney(v));
        this.animateValue("autoApprovedPct", this.kpis.autoApprovedPct, (v) => `${Math.round(v)}%`);
        this.animateValue("avgProcessingMs", this.kpis.avgProcessingMs, (v) => formatMs(Math.round(v)));
      },

      outcomeLabel(code) { return outcomeLabel(code); },
      badgeClass(status) { return badgeClass(status); },
      formatMoney(v) { return formatMoney(v); },
      formatMs(ms) { return formatMs(ms); },
      formatRunTime(iso) { return formatRunTime(iso); },
    };
  });
});
