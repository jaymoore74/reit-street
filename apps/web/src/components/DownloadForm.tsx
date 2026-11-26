"use client";

import { useState, type FormEvent } from "react";

type Option = { id: string; title: string; description: string };

/**
 * Posts to /api/download. With JavaScript, refusals show inline and the file is saved from the
 * response; without it, the browser posts the form and downloads the file (or shows the refusal).
 */
export function DownloadForm({ datasets, formats }: { datasets: Option[]; formats: string[] }) {
  const [dataset, setDataset] = useState(datasets[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch("/api/download", { method: "POST", body: new FormData(e.currentTarget) });
      if (!res.ok) {
        setMessage({ kind: "error", text: (await res.text()).trim() });
        return;
      }
      const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "download";
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      setMessage({ kind: "ok", text: `Downloaded ${name}` });
    } catch {
      setMessage({ kind: "error", text: "The download failed. Check your connection and try again." });
    } finally {
      setBusy(false);
    }
  }

  const isMetrics = dataset === "metrics" || dataset === "metrics_history";
  const dateLabel = dataset === "filings" ? "filing date" : "period end";
  return (
    <form action="/api/download" method="post" onSubmit={onSubmit} className="stack">
      <div className="form-row">
        <label>
          Dataset
          <select name="dataset" value={dataset} onChange={(e) => setDataset(e.target.value)}>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Format
          <select name="format" defaultValue={formats[0]}>
            {formats.map((f) => (
              <option key={f} value={f}>
                {f.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted small">{datasets.find((d) => d.id === dataset)?.description}</p>
      <label>
        Tickers (optional, separated by spaces or commas; empty means all in your coverage)
        <input type="text" name="tickers" placeholder="PLD O SPG" maxLength={5000} />
      </label>
      {isMetrics && (
        <div className="form-row">
          <label>
            Metric codes (optional)
            <input type="text" name="metrics" placeholder="TOTAL_REVENUE NET_INCOME" maxLength={5000} />
          </label>
          <label>
            Period types (optional)
            <input type="text" name="periodTypes" placeholder="Q FY" maxLength={100} />
          </label>
        </div>
      )}
      {dataset === "filings" && (
        <label>
          Form types (optional)
          <input type="text" name="forms" placeholder="10-K 10-Q 8-K" maxLength={500} />
        </label>
      )}
      {dataset !== "companies" && (
        <div className="form-row">
          <label>
            From {dateLabel} (optional)
            <input type="date" name="from" />
          </label>
          <label>
            To {dateLabel} (optional)
            <input type="date" name="to" />
          </label>
          {dataset === "metrics_history" && (
            <label>
              As known on (default today)
              <input type="date" name="asOf" />
            </label>
          )}
        </div>
      )}
      {message && (
        <p className={message.kind === "error" ? "error" : "ok"} role={message.kind === "error" ? "alert" : "status"}>
          {message.text}
        </p>
      )}
      <div>
        <button type="submit" disabled={busy}>
          {busy ? "Preparing..." : "Download"}
        </button>
      </div>
    </form>
  );
}
