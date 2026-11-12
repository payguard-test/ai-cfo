// Anchors one period's book fingerprints on NEAR testnet.
// Usage: node near/anchor.mjs <contract> <period> <books_root> <report_hash> <tx_count>
// Signs with ANCHOR_PRIVATE_KEY: a function-call key on the contract account limited to `anchor`.
import { readFileSync, existsSync } from "node:fs";
import { Account, JsonRpcProvider, KeyPairSigner, teraToGas } from "near-api-js";

if (existsSync(".env")) process.loadEnvFile(".env");
const [contract, period, booksRoot, reportHash, txCount] = process.argv.slice(2);
if (!contract || !period || !booksRoot || !reportHash || txCount === undefined) {
  console.error("usage: node near/anchor.mjs <contract> <period> <books_root> <report_hash> <tx_count>");
  process.exit(2);
}
const key = process.env.ANCHOR_PRIVATE_KEY;
if (!key) {
  console.error("ANCHOR_PRIVATE_KEY missing in .env (run: npm --prefix near run setup)");
  process.exit(2);
}
const provider = new JsonRpcProvider({ url: process.env.NEAR_RPC_URL || "https://test.rpc.fastnear.com" });
const signerAccount = process.env.ANCHOR_ACCOUNT_ID || contract;
const account = new Account(signerAccount, provider, KeyPairSigner.fromSecretKey(key));
let res;
try {
  res = await account.callFunctionRaw({
  contractId: contract,
  methodName: "anchor",
  args: { period, books_root: booksRoot, report_hash: reportHash, tx_count: Number(txCount) },
  gas: teraToGas("30"),
  deposit: 0n,
  });
} catch (e) {
  console.error(String(e?.message ?? e));
  process.exit(1);
}
const status = res?.status;
if (status && typeof status === "object" && "Failure" in status) {
  console.error(JSON.stringify(status.Failure));
  process.exit(1);
}
console.log(JSON.stringify({ tx: res?.transaction?.hash ?? res?.transaction_outcome?.id }));
