export const meta = {
  name: 'solana-verify-and-sweep',
  description: 'Batch-verify 44 new Solana incident records with facts and scope lenses, then sweep for still-missing incidents',
  phases: [
    { title: 'Verify', detail: 'facts + scope lens, 8 records per batch' },
    { title: 'Sweep', detail: 'search modalities for missing incidents' },
    { title: 'Dedupe', detail: 'merge sweep candidates' },
    { title: 'Write', detail: 'records in batches of 6' },
    { title: 'Verify new', detail: 'facts + scope lens per written batch' },
  ],
}

const REC_PATH = args.recordsPath
const EXISTING_PATH = args.existingPath

const RULES = `
DATABASE RULES (Solana ecosystem security-incident case library, Traditional Chinese for Taiwan readers):
Scope — INCLUDE: attacks/exploits on Solana on-chain programs; compromises of Solana wallets, trading bots/terminals, SDKs/frameworks; CEX/custodian/bridge/casino thefts where Solana assets were specifically stolen (chain_scope multi_chain, loss_usd = all-chain total); Solana L1 core vulnerabilities and protocol bugs that caused or could cause outages; deliberate spam/DoS against Solana infra; documented Solana-targeted phishing, drainer and supply-chain (npm/PyPI/crates/GitHub/extension) campaigns; significant responsible disclosures of critical bugs in Solana programs (loss 0).
EXCLUDE: memecoin rug pulls / insider dumps; pure capacity/hardware outages with no bug or attack; incidents on other chains; multi-chain incidents whose Solana share is negligible (< ~$50K); downstream exposures of an incident already listed (e.g. UXD/Tulip funds frozen by Mango — covered by mango-markets-2022) => duplicate, drop; any duplicate of a known incident.
Fields: id (kebab-case with year, unique), date (YYYY-MM-DD UTC; incident start, else disclosure), project, project_type, category, vuln_pattern, attack_vector (short English), loss_usd (USD at the time; 0 = confirmed no loss; null = unknown), assets_stolen, recovered_usd (only funds recovered/returned/frozen from the attacker, NOT reimbursements; <= loss_usd), recovery_status (none|partial|full|returned_by_attacker|reimbursed_by_backer|not_applicable|unknown; not_applicable iff loss_usd is 0), attribution ('Unknown' if none), chain_scope (solana_only | multi_chain), summary_zh / root_cause_zh / aftermath_zh (Traditional Chinese, Taiwan usage, NO Simplified characters), confidence (high|medium|low), notes (English; never mention research process/tools/sessions), sources (1-3 of {title, publisher, url}; real URLs only).
project_type: bridge|lending|dex_amm|perp_derivatives|stablecoin|yield_vault|wallet|cex|trading_bot|nft_gaming|launchpad|dao_governance|infrastructure_sdk|l1_runtime|staking|other.
category: smart_contract_bug (on-chain program OR platform/SDK/wallet/framework code bug) | oracle_price_manipulation | economic_exploit | private_key_compromise | insider_threat | supply_chain | phishing_social_engineering | governance_attack | frontend_account_compromise | third_party_compromise | protocol_vulnerability (ONLY Solana L1: validator clients, runtime, rBPF/SBF VM, native/core programs, consensus/networking, incl. outages from spam exploiting missing QoS) | dos_attack (floods against websites/wallet backends/network with no bug exploited and no protocol-weakness halt) | operational_error.
vuln_pattern: REQUIRED iff category is smart_contract_bug or protocol_vulnerability, else null. One of account_validation, reinitialization, arithmetic, control_flow, race_condition, crypto_verification, input_handling_dos, resource_exhaustion, consensus_state_bug, offchain_logic, undisclosed.
`
const TOOLS = 'Use WebSearch (load with ToolSearch "select:WebSearch,WebFetch" if not loaded). WebFetch is blocked for many domains; rely on search results then. NEVER invent URLs.'

const SOURCE = { type: 'object', properties: { title: { type: 'string' }, publisher: { type: 'string' }, url: { type: 'string' } }, required: ['title', 'publisher', 'url'] }
const FIELD_NAMES = ['id','date','project','project_type','category','vuln_pattern','attack_vector','loss_usd','assets_stolen','recovered_usd','recovery_status','attribution','chain_scope','summary_zh','root_cause_zh','aftermath_zh','confidence','notes','sources']
const RECORD = {
  type: 'object',
  properties: {
    id: { type: 'string' }, date: { type: 'string' }, project: { type: 'string' }, project_type: { type: 'string' },
    category: { type: 'string' }, vuln_pattern: { type: ['string', 'null'] }, attack_vector: { type: 'string' },
    loss_usd: { type: ['number', 'null'] }, assets_stolen: { type: 'string' }, recovered_usd: { type: ['number', 'null'] },
    recovery_status: { type: 'string' }, attribution: { type: 'string' }, chain_scope: { type: 'string' },
    summary_zh: { type: 'string' }, root_cause_zh: { type: 'string' }, aftermath_zh: { type: 'string' },
    confidence: { type: 'string' }, notes: { type: 'string' }, sources: { type: 'array', items: SOURCE },
    in_scope: { type: 'boolean' }, candidate_name: { type: 'string' },
  },
  required: FIELD_NAMES.concat(['in_scope', 'candidate_name']),
}
const RECORDS = { type: 'object', properties: { records: { type: 'array', items: RECORD } }, required: ['records'] }
const CANDIDATES = {
  type: 'object',
  properties: { candidates: { type: 'array', items: { type: 'object', properties: {
    name: { type: 'string' }, date: { type: 'string' }, summary: { type: 'string' }, source_urls: { type: 'array', items: { type: 'string' } },
  }, required: ['name', 'date', 'summary', 'source_urls'] } } },
  required: ['candidates'],
}
const VERDICTS = {
  type: 'object',
  properties: { verdicts: { type: 'array', items: { type: 'object', properties: {
    id: { type: 'string' }, verdict: { type: 'string', enum: ['keep', 'fix', 'drop'] }, reasons: { type: 'string' },
    corrections: { type: 'object', additionalProperties: true },
  }, required: ['id', 'verdict', 'reasons'] } } },
  required: ['verdicts'],
}

function chunk(arr, n) { const out = []; for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n)); return out }
const KNOWN = `Known incidents already in the database: ${EXISTING_PATH} (JSON array). Other new candidates under review: ${REC_PATH}.`

function lens(kind, recordsRef) {
  const body = `${RULES}\n${TOOLS}\n${KNOWN}\nRecords to check: ${recordsRef}\nReturn one verdict per record id.`
  if (kind === 'facts') return `${body}
YOUR LENS: FACTS. Be a skeptic: try to REFUTE each record's facts with independent searches (event happened, date, loss_usd, assets, recovered_usd = attacker funds only, attribution, root cause, aftermath) and confirm each source URL appears in search results and is about this event; check the Chinese text matches (Taiwan usage, no Simplified characters).
Verdicts: keep; fix = ONLY corrected fields in corrections (allowed: date, project, attack_vector, loss_usd, assets_stolen, recovered_usd, attribution, summary_zh, root_cause_zh, aftermath_zh, confidence, notes, sources — sources replaces the whole list; remove URLs without evidence); drop = cannot be substantiated.`
  return `${body}
YOUR LENS: SCOPE & CLASSIFICATION. Try to find reasons each record should NOT be included or is misclassified, applying the rules strictly: out of scope, duplicate of a known incident or another record (same event, or downstream exposure), negligible Solana share, wrong category (app/SDK/wallet bug labelled protocol_vulnerability; website DDoS not labelled dos_attack), wrong/missing vuln_pattern, wrong project_type / chain_scope / recovery_status, bad id.
Verdicts: keep; fix = corrections ONLY for id, project_type, category, vuln_pattern, chain_scope, recovery_status; drop = out of scope or duplicate (name the known id).`
}

async function verifyBatch(recordsRef, tag, phaseName) {
  const [facts, scope] = await parallel(['facts', 'scope'].map(k => () =>
    agent(lens(k, recordsRef), { phase: phaseName, label: `${k}:${tag}`, schema: VERDICTS })))
  return { facts: (facts && facts.verdicts) || [], scope: (scope && scope.verdicts) || [] }
}

// ---- Verify the 44 file-based records in batches of 8
const idBatches = chunk(args.ids, 8)
const verifyP = parallel(idBatches.map((ids, i) => () =>
  verifyBatch(`the objects with these "id" values inside ${REC_PATH}: ${ids.join(', ')}`, `b${i}`, 'Verify')))

// ---- Sweep for missing incidents
const MODALITIES = [
  { key: 'catalogs', prompt: 'Walk every Solana entry in Helius "Solana Hacks, Bugs, and Exploits: A Complete History" and "A Complete History of Solana Outages", DefiLlama hacks (Solana), rekt.news, SlowMist Hacked (Solana), De.Fi REKT (Solana), Web3IsGoingGreat (Solana tag), sannykim/solsec.' },
  { key: 'reports', prompt: 'Go month by month from January 2024 through September 2026 through crypto security monthly/quarterly reports (PeckShield, CertiK, Immunefi, SlowMist, Cyvers, Hacken, Beosin) and news, listing every Solana-related incident.' },
  { key: 'disclosures-phishing', prompt: 'Find Solana critical bug disclosures (Immunefi bug-fix reviews, OtterSec, Neodyme, Asymmetric Research, Sec3, Zellic, OShield, CertiK; GitHub advisories for Solana crates/packages) and Solana drainer / phishing / malicious-package / extension campaigns 2021-2026.' },
  { key: 'cex-defi', prompt: 'Find CEX/custodian/casino/payment hacks where Solana assets were stolen, Solana trading bot/terminal compromises, and Solana DeFi/NFT/gaming exploits 2021-2026 not yet known.' },
]
async function sweep() {
  const found = (await parallel(MODALITIES.map(m => () => agent(
    `${RULES}\n${TOOLS}\n${KNOWN} Read both files first and do not return any incident already in them.\nTask: find Solana security incidents NOT yet in the database. Approach: ${m.prompt}\nReturn up to 15 in-scope candidates with name, date, one-sentence English summary and 1-3 source URLs from your search results; empty list if nothing new.`,
    { phase: 'Sweep', label: `sweep:${m.key}`, schema: CANDIDATES })))).filter(Boolean).flatMap(r => r.candidates || [])
  log(`sweep: ${found.length} raw candidates`)
  if (!found.length) return { results: [], rejected: [] }
  const deduped = await agent(
    `${RULES}\n${KNOWN}\nDeduplicate these candidates: merge ones describing the same event; remove any that match a known incident (read both files), are downstream exposures of a known incident, or are out of scope.\nCandidates (JSON): ${JSON.stringify(found)}`,
    { phase: 'Dedupe', label: 'dedupe', schema: CANDIDATES })
  const fresh = (deduped && deduped.candidates) || []
  log(`sweep: ${fresh.length} fresh candidates`)
  const out = await pipeline(chunk(fresh, 6),
    (b, _o, i) => agent(`${RULES}\n${TOOLS}\n${KNOWN}\nWrite one complete record for EACH candidate, verifying every fact with web searches; put the candidate's name in candidate_name; set in_scope=false (reason in notes) if it is out of scope, a duplicate, or unsubstantiated.\nCandidates (JSON): ${JSON.stringify(b)}`,
      { phase: 'Write', label: `write:${i}`, schema: RECORDS }),
    async (w, _o, i) => {
      const recs = (w && w.records) || []
      const inScope = recs.filter(r => r.in_scope !== false)
      const rejected = recs.filter(r => r.in_scope === false).map(r => ({ candidate: r.candidate_name, reason: r.notes }))
      if (!inScope.length) return { results: [], rejected }
      const v = await verifyBatch(`this JSON list (not in any file): ${JSON.stringify(inScope)}`, `new${i}`, 'Verify new')
      const f = Object.fromEntries(v.facts.map(x => [x.id, x])), s = Object.fromEntries(v.scope.map(x => [x.id, x]))
      return { results: inScope.map(r => ({ record: r, facts: f[r.id] || null, scope: s[r.id] || null })), rejected }
    })
  return {
    results: out.filter(Boolean).flatMap(o => o.results),
    rejected: out.filter(Boolean).flatMap(o => o.rejected),
  }
}

const [verified, swept] = await Promise.all([verifyP, sweep()])
const facts = verified.filter(Boolean).flatMap(v => v.facts)
const scope = verified.filter(Boolean).flatMap(v => v.scope)
log(`verified ${facts.length} facts verdicts, ${scope.length} scope verdicts; sweep added ${swept.results.length}`)
return { facts, scope, sweep: swept }
