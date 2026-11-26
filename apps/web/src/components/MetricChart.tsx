"use client";

import { useId, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
  type TooltipValueType as ValueType,
} from "recharts";
import { formatValue, type Series, type SeriesPoint } from "@/lib/format";

/**
 * One metric over time for one company. Single series, so no legend: the selected metric's
 * name is the chart title. Colors come from CSS (globals.css, .chart rules) so light and dark
 * modes each use their own validated step. A table of the same numbers is one click away.
 */
export function MetricChart({ series }: { series: Series[] }) {
  const [key, setKey] = useState(series[0]?.key ?? "");
  const [showTable, setShowTable] = useState(false);
  const selectId = useId();
  const s = series.find((x) => x.key === key) ?? series[0];
  if (!s) return <p className="muted">No metrics in your subscription for this company.</p>;

  const renderTooltip = ({ active, payload }: TooltipContentProps<ValueType, string | number>) => {
    const p = active ? (payload?.[0]?.payload as SeriesPoint | undefined) : undefined;
    if (!p) return null;
    return (
      <div className="tooltip">
        <div className="muted">{p.period}</div>
        <div>{formatValue(p.value, s.unit)}</div>
      </div>
    );
  };
  const margin = { top: 8, right: 8, bottom: 0, left: 0 };
  const yFormat = (v: number) => formatValue(v, s.unit, true);

  return (
    <div>
      <div className="form-row" style={{ marginBottom: 12 }}>
        <label htmlFor={selectId}>
          Metric
          <select id={selectId} value={s.key} onChange={(e) => setKey(e.target.value)}>
            {series.map((x) => (
              <option key={x.key} value={x.key}>
                {x.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <h3 style={{ margin: "0 0 8px", fontSize: "1rem" }}>{s.label}</h3>
      <div
        className="chart"
        role="img"
        aria-label={`${s.label}, ${s.points.length} periods. Show as table lists every value.`}
      >
        <ResponsiveContainer width="100%" height="100%">
          {s.mark === "bar" ? (
            <BarChart data={s.points} margin={margin}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="period" tickLine={false} minTickGap={16} />
              <YAxis width={72} tickLine={false} axisLine={false} tickFormatter={yFormat} />
              <Tooltip content={renderTooltip} cursor={{ fillOpacity: 0.4 }} isAnimationActive={false} />
              <Bar
                dataKey="value"
                className="series-1"
                maxBarSize={24}
                radius={[4, 4, 0, 0]}
                isAnimationActive={false}
              />
            </BarChart>
          ) : (
            <LineChart data={s.points} margin={margin}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="period" tickLine={false} minTickGap={16} />
              <YAxis width={72} tickLine={false} axisLine={false} tickFormatter={yFormat} />
              <Tooltip content={renderTooltip} isAnimationActive={false} />
              <Line
                dataKey="value"
                className="series-1"
                type="linear"
                dot={{ r: 4 }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
              />
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
      <p>
        <button
          type="button"
          className="secondary"
          onClick={() => setShowTable((v) => !v)}
          aria-expanded={showTable}
        >
          {showTable ? "Hide table" : "Show as table"}
        </button>
      </p>
      {showTable && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Period</th>
                <th>Period end</th>
                <th className="num">Value</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {s.points.map((p) => (
                <tr key={p.periodEnd + p.period}>
                  <td>{p.period}</td>
                  <td>{p.periodEnd}</td>
                  <td className="num">{formatValue(p.value, s.unit)}</td>
                  <td>
                    {p.source?.startsWith("https://") ? (
                      <a href={p.source} target="_blank" rel="noopener noreferrer">
                        SEC filing
                      </a>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
