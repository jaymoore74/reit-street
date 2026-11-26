import "server-only";
import { readFileSync } from "node:fs";
import mysql, { type Pool, type RowDataPacket, type ResultSetHeader } from "mysql2/promise";
import { env } from "./env";

/**
 * Two pools, one login (rs_web, ADR 0013):
 *   warehouse  SELECT only, through the dl_* delivery views (the client contract)
 *   app        accounts, sessions, entitlements, research, newsletter, audit
 * All SQL is hand written with bound parameters. Never build SQL from request values.
 */
type Pools = { warehouse: Pool; app: Pool };
const g = globalThis as unknown as { __rsPools?: Pools };

function makePool(database: string): Pool {
  const e = env();
  return mysql.createPool({
    host: e.RS_DB_HOST,
    port: e.RS_DB_PORT,
    user: e.RS_DB_USER,
    password: e.RS_DB_PASSWORD,
    database,
    connectionLimit: e.RS_DB_POOL_SIZE,
    waitForConnections: true,
    timezone: "Z",
    dateStrings: true,
    decimalNumbers: false, // DECIMAL arrives as a string: no float rounding of financial values
    supportBigNumbers: true,
    bigNumberStrings: true,
    charset: "utf8mb4",
    ssl: e.RS_DB_SSL_CA ? { ca: readFileSync(e.RS_DB_SSL_CA, "utf8"), rejectUnauthorized: true } : undefined,
  });
}

function pools(): Pools {
  if (!g.__rsPools) {
    const e = env();
    const p = { warehouse: makePool(e.RS_DB_WAREHOUSE), app: makePool(e.RS_DB_APP) };
    for (const pool of [p.warehouse, p.app]) {
      pool.on("connection", (conn) => {
        conn.query("SET SESSION time_zone = '+00:00'");
      });
    }
    g.__rsPools = p;
  }
  return g.__rsPools;
}

export type Params = (string | number | null | Date)[];
export type Row = RowDataPacket & Record<string, unknown>;

export async function whQuery<T = Row>(sql: string, params: Params = []): Promise<T[]> {
  const [rows] = await pools().warehouse.query<RowDataPacket[]>(sql, params);
  return rows as unknown as T[];
}

export async function appQuery<T = Row>(sql: string, params: Params = []): Promise<T[]> {
  const [rows] = await pools().app.query<RowDataPacket[]>(sql, params);
  return rows as unknown as T[];
}

export async function appExec(sql: string, params: Params = []): Promise<ResultSetHeader> {
  const [res] = await pools().app.query<ResultSetHeader>(sql, params);
  return res;
}

/** Close pools (tests and graceful shutdown). */
export async function closePools(): Promise<void> {
  if (g.__rsPools) {
    await Promise.all([g.__rsPools.warehouse.end(), g.__rsPools.app.end()]);
    g.__rsPools = undefined;
  }
}
