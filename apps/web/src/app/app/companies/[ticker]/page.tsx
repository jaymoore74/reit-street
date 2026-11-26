import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { MetricChart } from "@/components/MetricChart";
import { companyFilings, companyMetrics, getCompany } from "@/lib/data";
import { buildSeries } from "@/lib/format";
import { entitlementsFor, requireUser } from "@/lib/session";

export const metadata: Metadata = { title: "Company" };

export default async function CompanyPage({ params }: { params: Promise<{ ticker: string }> }) {
  const ticker = decodeURIComponent((await params).ticker).toUpperCase();
  const user = await requireUser(`/app/companies/${encodeURIComponent(ticker)}`);
  const ent = await entitlementsFor(user.orgId);
  const company = await getCompany(ent, ticker);
  if (!company) notFound(); // outside coverage looks the same as unknown
  const [metrics, filings] = await Promise.all([companyMetrics(ent, ticker), companyFilings(ent, ticker)]);
  const series = buildSeries(metrics);

  return (
    <>
      <h1>
        {company.name} <span className="muted">({company.ticker})</span>
      </h1>
      <p className="muted">
        {[company.sector, company.reit_type, company.exchange].filter(Boolean).join(" · ")}
        {company.cik ? ` · SEC CIK ${company.cik}` : ""}
      </p>
      <div className="card">
        {ent.datasets.includes("metrics") ? (
          <MetricChart series={series} />
        ) : (
          <p className="muted">Your subscription doesn&apos;t include financial metrics.</p>
        )}
      </div>
      {ent.datasets.includes("filings") && (
        <div className="card table-wrap">
          <h2 style={{ marginTop: 0 }}>Recent SEC filings</h2>
          {filings.length ? (
            <table className="data">
              <thead>
                <tr>
                  <th>Filed</th>
                  <th>Form</th>
                  <th>Period</th>
                  <th>Filing</th>
                </tr>
              </thead>
              <tbody>
                {filings.map((f) => (
                  <tr key={f.accession_no}>
                    <td>{f.filing_date}</td>
                    <td>{f.form_type}</td>
                    <td>{f.report_date ?? ""}</td>
                    <td>
                      {f.index_url.startsWith("https://") ? (
                        <a href={f.index_url} target="_blank" rel="noopener noreferrer">
                          {f.accession_no}
                        </a>
                      ) : (
                        f.accession_no
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">No filings in your history window.</p>
          )}
        </div>
      )}
    </>
  );
}
