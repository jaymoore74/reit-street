import type { Metadata } from "next";
import { ChangePasswordForm, SignOutOthersForm, SubscriptionsForm } from "@/components/forms";
import { lists, mySubscriptions } from "@/lib/newsletter";
import { requireUser } from "@/lib/session";

export const metadata: Metadata = { title: "Account" };

export default async function AccountPage() {
  const user = await requireUser("/app/account");
  const [allLists, subs] = await Promise.all([lists(true), mySubscriptions(user.email)]);
  const subscribed = new Set(subs.filter((s) => s.status === "confirmed").map((s) => s.list_code));
  return (
    <>
      <h1>Account</h1>
      <div className="card">
        <p>
          {user.fullName ? `${user.fullName} · ` : ""}
          {user.email}
        </p>
        <p className="muted small">
          {user.orgName} · role: {user.role.replace("_", " ")}
        </p>
      </div>
      <div className="grid2">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Newsletters</h2>
          <SubscriptionsForm
            lists={allLists.map((l) => ({
              code: l.list_code,
              name: l.name,
              description: l.description,
              subscribed: subscribed.has(l.list_code),
            }))}
          />
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Password</h2>
          <ChangePasswordForm />
          <h2>Sessions</h2>
          <SignOutOthersForm />
        </div>
      </div>
    </>
  );
}
