import { describe, expect, it } from "vitest";
import { sesInput } from "@/lib/email";
import { newsletterConfirmEmail, passwordResetEmail } from "@/lib/email-templates";

describe("emails", () => {
  it("reset email carries the link, the expiry and the site name", () => {
    const m = passwordResetEmail("Test Site", "ann@acme.example", "https://x.example/reset/TOKEN", 60);
    expect(m.to).toBe("ann@acme.example");
    expect(m.subject).toBe("Reset your Test Site password");
    expect(m.text).toContain("https://x.example/reset/TOKEN");
    expect(m.text).toContain("60 minutes");
    expect(m.text).not.toMatch(/[–—]/);
  });

  it("newsletter confirmation names the list and asks for a press", () => {
    const m = newsletterConfirmEmail("Test Site", "r@x.example", "Weekly", "https://x.example/newsletter/confirm/T", 7);
    expect(m.text).toContain('"Weekly"');
    expect(m.text).toContain("press Confirm");
    expect(m.text).toContain("7 days");
  });

  it("SES request is plain text to one recipient", () => {
    expect(sesInput("research@x.example", { to: "a@b.example", subject: "S", text: "T" })).toEqual({
      FromEmailAddress: "research@x.example",
      Destination: { ToAddresses: ["a@b.example"] },
      Content: { Simple: { Subject: { Data: "S", Charset: "UTF-8" }, Body: { Text: { Data: "T", Charset: "UTF-8" } } } },
    });
  });
});
