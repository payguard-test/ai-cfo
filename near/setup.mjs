// One-time NEAR testnet setup for AI CFO.
//   npm --prefix near install && npm --prefix near run setup
// Needs OWNER_ACCOUNT_ID / OWNER_PRIVATE_KEY in ../.env (a funded .testnet account, ~2.5 NEAR).
// Creates <name>.testnet, deploys contracts/books-anchor, makes the owner its admin, gives the
// app a function-call key that can only call `anchor`, deletes the account's full-access key,
// and appends BOOKS_CONTRACT_ID / ANCHOR_ACCOUNT_ID / ANCHOR_PRIVATE_KEY to ../.env.
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { Account, JsonRpcProvider, KeyPair, KeyPairSigner, nearToYocto, teraToGas } from "near-api-js";

const ENV = new URL("../.env", import.meta.url).pathname;
const WASM = new URL("../contracts/books-anchor/target/near/books_anchor.wasm", import.meta.url).pathname;
if (existsSync(ENV)) process.loadEnvFile(ENV);
const need = (k) => process.env[k] || (console.error(`${k} missing in .env`), process.exit(2));
const ownerId = need("OWNER_ACCOUNT_ID");
if (!ownerId.endsWith(".testnet")) { console.error("OWNER_ACCOUNT_ID must be a .testnet account"); process.exit(2); }
if (process.env.BOOKS_CONTRACT_ID && !process.argv.includes("--force")) {
  console.error(`BOOKS_CONTRACT_ID is already set (${process.env.BOOKS_CONTRACT_ID}). Pass --force to create a new one.`);
  process.exit(2);
}
if (!existsSync(WASM)) { console.error(`${WASM} not found. Build it: cd contracts/books-anchor && cargo near build non-reproducible-wasm --no-abi`); process.exit(2); }

const provider = new JsonRpcProvider({ url: process.env.NEAR_RPC_URL || "https://test.rpc.fastnear.com" });
const owner = new Account(ownerId, provider, KeyPairSigner.fromSecretKey(need("OWNER_PRIVATE_KEY")));
const name = `aicfo-${Math.random().toString(36).slice(2, 7)}.testnet`;
const full = KeyPair.fromRandom("ed25519");
const appKey = KeyPair.fromRandom("ed25519");
const business = process.env.BUSINESS_NAME || "Demo Business Ltd";
const log = (m) => console.log(`• ${m}`);

await owner.createAccount({ newAccountId: name, publicKey: full.getPublicKey(), nearToTransfer: nearToYocto("2") });
log(`created ${name}`);
const contract = new Account(name, provider, new KeyPairSigner(full));
await contract.deployContract(new Uint8Array(readFileSync(WASM)));
await contract.callFunction({ contractId: name, methodName: "new", args: { owner_id: ownerId, business }, gas: teraToGas("30") });
log(`deployed books-anchor, owner ${ownerId}`);
await owner.callFunction({ contractId: name, methodName: "set_writer", args: { account_id: name, allowed: true }, gas: teraToGas("30"), deposit: 1n });
await contract.addFunctionCallAccessKey({ publicKey: appKey.getPublicKey(), contractId: name, methodNames: ["anchor"], allowance: nearToYocto("0.25") });
await contract.deleteKey(full.getPublicKey());
log("app key can only call `anchor`; full-access key deleted");
const updates = { BOOKS_CONTRACT_ID: name, ANCHOR_ACCOUNT_ID: name, ANCHOR_PRIVATE_KEY: appKey.toString() };
let envText = existsSync(ENV) ? readFileSync(ENV, "utf8") : "";
for (const [k, v] of Object.entries(updates)) {
  const re = new RegExp(`^${k}=.*$`, "m");
  envText = re.test(envText) ? envText.replace(re, `${k}=${v}`) : envText + `${envText.endsWith("\n") ? "" : "\n"}${k}=${v}\n`;
}
writeFileSync(ENV, envText);
log(`saved to .env. Explorer: https://testnet.nearblocks.io/address/${name}`);
