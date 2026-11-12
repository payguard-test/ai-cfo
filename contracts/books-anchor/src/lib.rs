//! books-anchor: tamper-evident fingerprints of a small business's books on NEAR.
//!
//! For each accounting period the AI CFO posts two SHA-256 hashes: the root of every
//! transaction in the ledger, and the hash of the financial statements built from
//! them. No amounts, names or account numbers ever go on chain. Later, anyone who is
//! shown the books (a lender, an investor, an auditor) can recompute the hashes and
//! check they match what was anchored at the time, so the books cannot be quietly
//! rewritten. Each re-anchor of a period is kept as history.
//!
//! The app signs with a function-call key limited to `anchor`; only the owner (full
//! access key, 1 yoctoNEAR) can add or remove writers.

use near_sdk::json_types::U64;
use near_sdk::serde_json::json;
use near_sdk::store::{IterableMap, LookupSet};
use near_sdk::{assert_one_yocto, env, near, require, AccountId, BorshStorageKey, PanicOnDefault};

pub const MAX_HISTORY: usize = 50;

#[near(serializers = [borsh])]
#[derive(BorshStorageKey)]
enum StorageKey {
    Periods,
    Writers,
}

#[near(serializers = [borsh, json])]
#[derive(Clone)]
pub struct Anchor {
    pub books_root: String,
    pub report_hash: String,
    pub tx_count: u32,
    pub anchored_at: U64,
    pub anchored_by: AccountId,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct BooksAnchor {
    owner_id: AccountId,
    business: String,
    writers: LookupSet<AccountId>,
    periods: IterableMap<String, Vec<Anchor>>,
}

fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
}

/// "YYYY-MM" or "all".
fn is_period(p: &str) -> bool {
    if p == "all" {
        return true;
    }
    let b = p.as_bytes();
    b.len() == 7 && b[4] == b'-' && b.iter().enumerate().all(|(i, c)| i == 4 || c.is_ascii_digit()) && matches!(&p[5..7], "01" | "02" | "03" | "04" | "05" | "06" | "07" | "08" | "09" | "10" | "11" | "12")
}

#[near]
impl BooksAnchor {
    #[init]
    pub fn new(owner_id: AccountId, business: String) -> Self {
        require!(!business.is_empty() && business.len() <= 80, "business name 1..80 chars");
        let mut writers = LookupSet::new(StorageKey::Writers);
        writers.insert(owner_id.clone());
        Self { owner_id, business, writers, periods: IterableMap::new(StorageKey::Periods) }
    }

    /// Record the fingerprints of one period's books.
    pub fn anchor(&mut self, period: String, books_root: String, report_hash: String, tx_count: u32) -> Anchor {
        let by = env::predecessor_account_id();
        require!(self.writers.contains(&by), "not an allowed writer");
        require!(is_period(&period), "period must be YYYY-MM or all");
        require!(is_hex64(&books_root), "books_root must be lowercase sha256 hex");
        require!(is_hex64(&report_hash), "report_hash must be lowercase sha256 hex");
        let a = Anchor { books_root, report_hash, tx_count, anchored_at: U64(env::block_timestamp()), anchored_by: by };
        let mut hist = self.periods.get(&period).cloned().unwrap_or_default();
        if hist.len() >= MAX_HISTORY {
            hist.remove(0);
        }
        hist.push(a.clone());
        self.periods.insert(period.clone(), hist);
        env::log_str(&format!(
            "EVENT_JSON:{}",
            json!({"standard": "books-anchor", "version": "1.0.0", "event": "anchor",
                   "data": [{"period": period, "books_root": a.books_root, "report_hash": a.report_hash, "tx_count": a.tx_count}]})
        ));
        a
    }

    #[payable]
    pub fn set_writer(&mut self, account_id: AccountId, allowed: bool) {
        assert_one_yocto();
        require!(env::predecessor_account_id() == self.owner_id, "owner only");
        if allowed {
            self.writers.insert(account_id);
        } else {
            self.writers.remove(&account_id);
        }
    }

    // ---------- views ----------

    pub fn get_latest(&self, period: String) -> Option<Anchor> {
        self.periods.get(&period).and_then(|h| h.last().cloned())
    }

    pub fn get_history(&self, period: String) -> Vec<Anchor> {
        self.periods.get(&period).cloned().unwrap_or_default()
    }

    pub fn list_periods(&self) -> Vec<String> {
        self.periods.keys().cloned().collect()
    }

    /// True if these hashes match what was last anchored for the period.
    pub fn verify(&self, period: String, books_root: String, report_hash: String) -> bool {
        self.get_latest(period).map(|a| a.books_root == books_root && a.report_hash == report_hash).unwrap_or(false)
    }

    pub fn get_business(&self) -> String {
        self.business.clone()
    }

    pub fn get_owner(&self) -> AccountId {
        self.owner_id.clone()
    }

    pub fn is_writer(&self, account_id: AccountId) -> bool {
        self.writers.contains(&account_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::{testing_env, NearToken};

    fn acc(s: &str) -> AccountId {
        s.parse().unwrap()
    }
    fn ctx(pred: &str, deposit: u128, now: u64) {
        let mut b = VMContextBuilder::new();
        b.current_account_id(acc("books.testnet"))
            .predecessor_account_id(acc(pred))
            .attached_deposit(NearToken::from_yoctonear(deposit))
            .block_timestamp(now);
        testing_env!(b.build());
    }
    fn h(c: char) -> String {
        c.to_string().repeat(64)
    }
    fn setup() -> BooksAnchor {
        ctx("owner.testnet", 0, 1);
        BooksAnchor::new(acc("owner.testnet"), "Demo Foods".into())
    }

    #[test]
    fn owner_can_anchor_and_verify() {
        let mut c = setup();
        ctx("owner.testnet", 0, 5);
        c.anchor("2026-11".into(), h('a'), h('b'), 19);
        assert!(c.verify("2026-11".into(), h('a'), h('b')));
        assert!(!c.verify("2026-11".into(), h('a'), h('c')));
        assert!(!c.verify("2026-10".into(), h('a'), h('b')));
        let l = c.get_latest("2026-11".into()).unwrap();
        assert_eq!(l.tx_count, 19);
        assert_eq!(l.anchored_at.0, 5);
        assert_eq!(c.list_periods(), vec!["2026-11".to_string()]);
    }

    #[test]
    fn reanchor_keeps_history_and_latest_wins() {
        let mut c = setup();
        ctx("owner.testnet", 0, 5);
        c.anchor("2026-11".into(), h('a'), h('b'), 1);
        c.anchor("2026-11".into(), h('c'), h('d'), 2);
        assert_eq!(c.get_history("2026-11".into()).len(), 2);
        assert!(c.verify("2026-11".into(), h('c'), h('d')));
        assert!(!c.verify("2026-11".into(), h('a'), h('b')));
    }

    #[test]
    #[should_panic(expected = "not an allowed writer")]
    fn stranger_cannot_anchor() {
        let mut c = setup();
        ctx("mallory.testnet", 0, 5);
        c.anchor("2026-11".into(), h('a'), h('b'), 1);
    }

    #[test]
    fn owner_can_add_writer() {
        let mut c = setup();
        ctx("owner.testnet", 1, 5);
        c.set_writer(acc("app.testnet"), true);
        ctx("app.testnet", 0, 6);
        c.anchor("all".into(), h('a'), h('b'), 1);
        assert!(c.is_writer(acc("app.testnet")));
    }

    #[test]
    #[should_panic(expected = "Requires attached deposit of exactly 1 yoctoNEAR")]
    fn set_writer_needs_full_access_key() {
        let mut c = setup();
        ctx("owner.testnet", 0, 5);
        c.set_writer(acc("app.testnet"), true);
    }

    #[test]
    #[should_panic(expected = "books_root must be lowercase sha256 hex")]
    fn rejects_non_hash() {
        let mut c = setup();
        ctx("owner.testnet", 0, 5);
        c.anchor("2026-11".into(), "NGN 500000 paid to Bello".into(), h('b'), 1);
    }

    #[test]
    #[should_panic(expected = "period must be YYYY-MM or all")]
    fn rejects_bad_period() {
        let mut c = setup();
        ctx("owner.testnet", 0, 5);
        c.anchor("2026-13".into(), h('a'), h('b'), 1);
    }
}
