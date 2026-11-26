import { randomBytes, scrypt as scryptCb, timingSafeEqual, type ScryptOptions } from "node:crypto";

/**
 * Password hashing with scrypt from Node's standard library (no native add-ons).
 * Parameters follow OWASP's scrypt guidance (N=2^15, r=8, p=3: 32 MiB, a few tenths of a second).
 * Stored as  scrypt$<log2 N>$<r>$<p>$<salt base64>$<hash base64>  so parameters can be raised later:
 * needsRehash() tells the sign-in code to re-hash with the current parameters.
 */
const LOG_N = 15;
const R = 8;
const P = 3;
const KEYLEN = 32;
const SALT_BYTES = 16;

export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 256;

function scrypt(password: string, salt: Buffer, logN: number, r: number, p: number): Promise<Buffer> {
  const N = 2 ** logN;
  const opts: ScryptOptions = { N, r, p, maxmem: 256 * N * r + 1024 * 1024 };
  return new Promise((resolve, reject) =>
    scryptCb(password.normalize("NFKC"), salt, KEYLEN, opts, (err, key) => (err ? reject(err) : resolve(key))),
  );
}

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(SALT_BYTES);
  const key = await scrypt(password, salt, LOG_N, R, P);
  return ["scrypt", LOG_N, R, P, salt.toString("base64"), key.toString("base64")].join("$");
}

function parse(stored: string) {
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return null;
  const [, logN, r, p, salt, hash] = parts;
  const nums = [Number(logN), Number(r), Number(p)];
  if (nums.some((n) => !Number.isInteger(n) || n < 1) || nums[0]! > 20) return null;
  return { logN: nums[0]!, r: nums[1]!, p: nums[2]!, salt: Buffer.from(salt!, "base64"), hash: Buffer.from(hash!, "base64") };
}

export async function verifyPassword(password: string, stored: string | null): Promise<boolean> {
  const parsed = stored ? parse(stored) : null;
  if (!parsed) {
    // same work as a real check, so a missing account takes as long as a wrong password
    await scrypt(password, randomBytes(SALT_BYTES), LOG_N, R, P);
    return false;
  }
  const key = await scrypt(password, parsed.salt, parsed.logN, parsed.r, parsed.p);
  return key.length === parsed.hash.length && timingSafeEqual(key, parsed.hash);
}

export function needsRehash(stored: string): boolean {
  const parsed = parse(stored);
  return !parsed || parsed.logN < LOG_N || parsed.r !== R || parsed.p < P;
}

/** NIST 800-63B style: length, not composition rules. Returns an error message or null. */
export function passwordProblem(password: string, email?: string): string | null {
  if (password.length < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`;
  if (password.length > PASSWORD_MAX) return `Use at most ${PASSWORD_MAX} characters.`;
  if (email && password.toLowerCase().includes(email.toLowerCase().split("@")[0]!) && email.split("@")[0]!.length >= 4)
    return "Don't use your email name in your password.";
  if (/^(.)\1+$/.test(password)) return "Don't use one character repeated.";
  return null;
}
