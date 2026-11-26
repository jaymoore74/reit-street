import { describe, expect, it } from "vitest";
import { hashPassword, needsRehash, passwordProblem, verifyPassword } from "@/lib/password";
import { clientIpFrom, sameOrigin } from "@/lib/request";
import { newToken, sha256Hex, ulid } from "@/lib/tokens";
import { safeNext } from "@/app/actions";

describe("password", () => {
  it("hashes with a salt and verifies", async () => {
    const a = await hashPassword("correct horse battery staple");
    const b = await hashPassword("correct horse battery staple");
    expect(a).not.toBe(b);
    expect(a).toMatch(/^scrypt\$15\$8\$3\$/);
    expect(await verifyPassword("correct horse battery staple", a)).toBe(true);
    expect(await verifyPassword("correct horse battery stapl", a)).toBe(false);
    expect(needsRehash(a)).toBe(false);
    expect(needsRehash("scrypt$14$8$1$AAAA$BBBB")).toBe(true);
  });
  it("treats a missing or malformed hash as a failure", async () => {
    expect(await verifyPassword("anything at all", null)).toBe(false);
    expect(await verifyPassword("anything at all", "plain")).toBe(false);
    expect(await verifyPassword("anything at all", "scrypt$40$8$1$AA$BB")).toBe(false);
  });
  it("checks length and obvious problems", () => {
    expect(passwordProblem("short")).toMatch(/12/);
    expect(passwordProblem("aaaaaaaaaaaaaaa")).toMatch(/repeated/);
    expect(passwordProblem("jason.moore-2026!", "jason.moore@example.com")).toMatch(/email/);
    expect(passwordProblem("four words that fit together")).toBeNull();
  });
});

describe("tokens", () => {
  it("are long, random and hashed", () => {
    const t = newToken();
    expect(t).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(newToken()).not.toBe(t);
    expect(sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
  it("ulid is 26 characters and sorts by time", () => {
    const a = ulid(1_700_000_000_000);
    const b = ulid(1_700_000_000_001);
    expect(a).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(a.slice(0, 10) < b.slice(0, 10)).toBe(true);
  });
});

describe("request helpers", () => {
  it("takes the client address the trusted proxy saw, never a spoofed one", () => {
    expect(clientIpFrom("6.6.6.6, 203.0.113.9", 1)).toBe("203.0.113.9");
    expect(clientIpFrom("203.0.113.9", 0)).toBe("unknown");
    expect(clientIpFrom(null, 1)).toBe("unknown");
    expect(clientIpFrom("<script>", 1)).toBe("unknown");
  });
  it("checks origin exactly", () => {
    expect(sameOrigin("https://reitstreet.example", "https://reitstreet.example")).toBe(true);
    expect(sameOrigin("https://reitstreet.example.evil.com", "https://reitstreet.example")).toBe(false);
    expect(sameOrigin(null, "https://reitstreet.example")).toBe(false);
  });
  it("only redirects to pages under /app after sign-in", async () => {
    expect(await safeNext("/app/data")).toBe("/app/data");
    expect(await safeNext("/app?x=1")).toBe("/app?x=1");
    expect(await safeNext("//evil.com")).toBe("/app");
    expect(await safeNext("https://evil.com")).toBe("/app");
    expect(await safeNext("/application")).toBe("/app");
    expect(await safeNext("/app\\evil")).toBe("/app");
  });
});
