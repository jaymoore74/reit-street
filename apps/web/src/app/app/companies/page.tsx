import type { Metadata } from "next";
import Link from "next/link";
import { listCompanies } from "@/lib/data";
import { entitlementsFor, requireUser } from "@/lib/session";

export const metadata: Metadata = { title: "Companies" };

export default async function CompaniesPage() {
  const user = await requireUser("/app/companies");
  const ent = await entitlementsFor(user.orgId);
  const companies = await listCompanies(ent);
  return (
    <>
      <h1>Companies</h1>
      {!ent.datasets.includes("companies") ? (
        <p className="muted">Your subscription doesn&apos;t include company data.</p>
      ) : companies.length === 0 ? (
        <p className="muted">No companies in your coverage yet.</p>
      ) : (
        <div className="card table-wrap">
          <p className="muted small">{companies.length} companies in your coverage.</p>
          <table className="data">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Company</th>
                <th>Sector</th>
                <th>Type</th>
                <th>Exchange</th>
              </tr>
            </thead>
            <tbody>
              {companies.map((c) => (
                <tr key={c.ticker}>
                  <td>
                    <Link href={`/app/companies/${encodeURIComponent(c.ticker)}`}>{c.ticker}</Link>
                  </td>
                  <td>{c.name}</td>
                  <td>{c.sector ?? ""}</td>
                  <td>{c.reit_type ?? ""}</td>
                  <td>{c.exchange ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
