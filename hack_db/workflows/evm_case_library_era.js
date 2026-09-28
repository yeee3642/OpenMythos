export const meta = {
  name: 'evm-case-library-era',
  description: 'Discover, write and adversarially verify EVM-chain security incidents (Ethereum or BSC) for one date range',
  whenToUse: 'Building the ethereum_hacks / bsc_hacks case libraries, one era per run',
  phases: [
    { title: 'Discover', detail: 'period finders + cross-cutting finders' },
    { title: 'Dedupe', detail: 'merge candidates per year' },
    { title: 'Write', detail: 'records in batches of 6' },
    { title: 'Verify', detail: 'facts lens + scope lens per batch' },
    { title: 'Critic', detail: 'find notable missing incidents, loop until dry' },
  ],
}

const C = args
const IS_ETH = C.chain === 'ethereum'
const CHAIN = C.chainName
const SINGLE_SCOPE = IS_ETH ? 'ethereum_only' : 'bsc_only'

const SCOPE_RULES = IS_ETH ? `
Scope — INCLUDE: incidents where Ethereum MAINNET contracts were exploited or Ethereum mainnet assets (ETH / ERC-20 / NFTs on L1) were stolen; bridges whose Ethereum-side contracts were drained or that lost Ethereum-side assets; CEX/custodian/payment/gaming hot-wallet thefts that included Ethereum mainnet assets (chain_scope multi_chain if other chains also hit; loss_usd = all-chain total); Ethereum L1 vulnerabilities (execution/consensus clients such as Geth, Nethermind, Besu, Erigon, Parity/OpenEthereum, Prysm, Lighthouse, Teku, Nimbus, Lodestar; EVM/precompiles; gas mispricing DoS; chain splits; finality incidents) and compiler/language bugs (Solidity, Vyper); Ethereum-targeted phishing / wallet-drainer / permit-signature / address-poisoning campaigns and supply-chain / frontend / DNS attacks with documented losses; critical responsibly disclosed bugs with >= $1M at risk.
EXCLUDE: incidents only on L2s or other chains (Arbitrum, Optimism, Base, zkSync, Polygon, BSC, Avalanche, Fantom, etc.) with no Ethereum mainnet assets lost; pure rug pulls / exit scams / Ponzi collapses with no hack; market crashes; small incidents below ~$1M unless notable (first of its kind, widely covered, or a critical disclosure).` : `
Scope — INCLUDE: incidents where BNB Smart Chain (BSC, chain id 56) contracts were exploited or BSC assets (BNB / BEP-20 / NFTs on BSC) were stolen, including the BSC Token Hub / BNB Beacon Chain cross-chain bridge; bridges whose BSC-side contracts were drained or that lost BSC-side assets; CEX/custodian/payment/gaming hot-wallet thefts that included BSC assets (chain_scope multi_chain if other chains also hit; loss_usd = all-chain total); BSC node client (bsc-geth / Erigon-BSC) and consensus vulnerabilities; BSC-targeted phishing / drainer / approval / address-poisoning campaigns and supply-chain / frontend / DNS attacks with documented losses; critical responsibly disclosed bugs with >= $1M at risk.
EXCLUDE: incidents only on Ethereum, opBNB, Greenfield or other chains with no BSC assets lost; pure rug pulls / exit scams / Ponzi collapses with no hack; market crashes; small incidents below ~$1M unless notable (first of its kind, widely covered, or a critical disclosure).`

const RULES = `
DATABASE RULES — ${CHAIN} security-incident case library (records written for Taiwan readers).
${SCOPE_RULES}
Fields: id (kebab-case project-year, add -mm if needed for uniqueness), date (YYYY-MM-DD UTC; incident start, else disclosure), project, project_type, category, vuln_pattern, attack_vector (short English), loss_usd (USD value at the time; 0 = confirmed no loss; null = unknown), assets_stolen, recovered_usd (ONLY funds recovered / returned / frozen from the attacker — NOT project or backer reimbursements; must not exceed loss_usd), recovery_status (none|partial|full|returned_by_attacker|reimbursed_by_backer|not_applicable|unknown; not_applicable iff loss_usd is 0), attribution ('Unknown' if none; e.g. 'Lazarus Group (DPRK), per FBI'), chain_scope (${SINGLE_SCOPE} = attack happened on ${CHAIN} even if laundered cross-chain; multi_chain = ${CHAIN} was one of several chains hit), summary_zh (2-3 sentences) / root_cause_zh / aftermath_zh (1-2 sentences each) in Traditional Chinese with Taiwan usage and NO Simplified characters, confidence (high|medium|low), notes (English; amount ranges and source discrepancies; never mention research process, tools or sessions), sources (1-3 of {title, publisher, url}; prefer official post-mortems, rekt.news, Chainalysis, TRM, Elliptic, SlowMist, PeckShield, CertiK, BlockSec, Immunefi, Halborn, DOJ/FBI, CoinDesk, The Block, Decrypt).
project_type: bridge|lending|dex_amm|perp_derivatives|stablecoin|yield_vault|wallet|cex|trading_bot|nft_gaming|launchpad|dao_governance|infrastructure_sdk|l1_runtime|staking|other.
category: smart_contract_bug (contract or platform/wallet/SDK code logic bug) | oracle_price_manipulation | economic_exploit (flash-loan / economic design abuse where code works as written) | private_key_compromise | insider_threat | supply_chain | phishing_social_engineering | governance_attack | frontend_account_compromise | third_party_compromise | protocol_vulnerability (ONLY chain L1: clients, EVM/precompiles, consensus, gas mispricing) | dos_attack (traffic floods, no bug) | operational_error.
vuln_pattern: REQUIRED when category is smart_contract_bug or protocol_vulnerability, otherwise null. One of: reentrancy, access_control, input_validation (unvalidated input / arbitrary external call, e.g. draining users' approvals), signature_message_verification (signature replay, ecrecover zero address, forged cross-chain messages), arithmetic (overflow, rounding, precision, share-inflation / empty-market donation attacks), business_logic, proxy_upgrade (uninitialized implementation, storage collision, bad upgrade/initialization, library selfdestruct), compiler_bug, crypto_verification, input_handling_dos, consensus_state_bug, resource_exhaustion, offchain_logic, undisclosed.
`
const TOOLS = 'Use WebSearch (load with ToolSearch "select:WebSearch,WebFetch" if not loaded). WebFetch is blocked for many domains; rely on search results then. NEVER invent URLs — only cite URLs that appeared in search results or that you fetched.'

const FIELD_NAMES = ['id','date','project','project_type','category','vuln_pattern','attack_vector','loss_usd','assets_stolen','recovered_usd','recovery_status','attribution','chain_scope','summary_zh','root_cause_zh','aftermath_zh','confidence','notes','sources']
const SOURCE = { type: 'object', properties: { title: { type: 'string' }, publisher: { type: 'string' }, url: { type: 'string' } }, required: ['title', 'publisher', 'url'] }
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
  properties: {
    candidates: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' }, date: { type: 'string' }, loss_usd_estimate: { type: ['number', 'null'] },
          summary: { type: 'string' }, source_urls: { type: 'array', items: { type: 'string' } },
        },
        required: ['name', 'date', 'summary', 'source_urls'],
      },
    },
  },
  required: ['candidates'],
}
const VERDICTS = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          verdict: { type: 'string', enum: ['keep', 'fix', 'drop'] },
          reasons: { type: 'string' },
          corrections: { type: 'object', additionalProperties: true },
        },
        required: ['id', 'verdict', 'reasons'],
      },
    },
  },
  required: ['verdicts'],
}

const ERA = `${C.from} to ${C.to}`
const CATALOGS = 'rekt.news (leaderboard and posts), DefiLlama hacks dashboard (filter by chain), SlowMist Hacked database, De.Fi REKT database, Web3IsGoingGreat, CertiK / PeckShield / Immunefi / SlowMist / Hacken / Beosin monthly, quarterly and annual reports, Chainalysis / TRM / Elliptic reports, and news coverage'

function finderPrompt(focus, from, to) {
  return `${RULES}\n${TOOLS}\nTask: list ${CHAIN} security incidents dated ${from} to ${to} for a case library. Focus: ${focus}\nCover systematically; use ${CATALOGS}. Return up to 40 in-scope candidates (largest losses first), each with name, date (YYYY-MM-DD best estimate), loss_usd_estimate, a one-sentence English summary, and 1-3 source URLs from your search results. Do not include out-of-scope items.`
}

const FINDERS = C.periods.map(p => ({
  key: `period:${p.name}`,
  prompt: finderPrompt(`EVERY hack / exploit / theft on ${CHAIN} in this period (DeFi protocols, bridges, exchanges, wallets, NFT, gaming, infrastructure).`, p.from, p.to),
})).concat([
  { key: 'cex-bridge', prompt: finderPrompt(`centralized exchanges, custodians, payment processors, casinos and cross-chain bridges that lost ${CHAIN} assets (hot-wallet / key compromises, bridge exploits).`, C.from, C.to) },
  { key: 'phishing-supply', prompt: finderPrompt(`phishing, wallet drainers (e.g. Inferno, Pink, Angel, Monkey drainers), permit / approval signature scams, address poisoning, malicious packages, supply-chain, frontend / DNS / account takeover attacks with documented losses to ${CHAIN} users.`, C.from, C.to) },
  { key: 'core-disclosures', prompt: finderPrompt(`${CHAIN} L1 client / consensus / EVM / gas / compiler (Solidity, Vyper) vulnerabilities and incidents, plus critical responsibly disclosed bugs (Immunefi bug-fix reviews, auditor disclosures) with >= $1M at risk.`, C.from, C.to) },
  { key: 'governance-insider', prompt: finderPrompt(`governance attacks, insider theft, admin / deployer key compromises and malicious upgrades of DeFi protocols on ${CHAIN}.`, C.from, C.to) },
])

function yearOf(d) { const y = parseInt(String(d || '').slice(0, 4), 10); return isNaN(y) ? null : y }
function chunk(arr, n) { const out = []; for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n)); return out }

function writePrompt(batch) {
  return `${RULES}\n${TOOLS}\nWrite one complete database record for EACH candidate below, researching and verifying every fact with web searches. Put the candidate's name in candidate_name. If a candidate turns out to be out of scope, a duplicate of another candidate in this batch, or unsubstantiated, still return a record with in_scope=false and the reason in notes.\nCandidates (JSON): ${JSON.stringify(batch)}`
}
function verifyPrompt(lens, records) {
  const body = `${RULES}\n${TOOLS}\nRecords to check (JSON): ${JSON.stringify(records)}\nReturn one verdict per record id.`
  if (lens === 'facts') return `${body}
YOUR LENS: FACTS. Be a skeptic and try to REFUTE each record's factual claims with independent web searches: that the event happened, date, loss_usd and assets, recovered_usd (attacker funds only), attribution, root cause, aftermath, and that every source URL appears in search results and is about this event. Check the Traditional Chinese text matches the facts (Taiwan usage, no Simplified characters).
Verdicts: keep = facts hold; fix = put ONLY corrected fields in corrections (allowed: date, project, attack_vector, loss_usd, assets_stolen, recovered_usd, attribution, summary_zh, root_cause_zh, aftermath_zh, confidence, notes, sources — sources replaces the whole list; drop URLs you cannot find evidence for; lower confidence when evidence is thin); drop = the event cannot be substantiated.`
  return `${body}
YOUR LENS: SCOPE & CLASSIFICATION. Try to find reasons each record should NOT be included or is misclassified, applying the DATABASE RULES strictly: out of scope (wrong chain, rug pull, below threshold and not notable), duplicate of another record in this list, wrong category / vuln_pattern (required iff smart_contract_bug or protocol_vulnerability) / project_type / chain_scope / recovery_status, or a bad id.
Verdicts: keep; fix = corrections ONLY for id, project_type, category, vuln_pattern, chain_scope, recovery_status; drop = out of scope or duplicate (say of what).`
}

async function writeAndVerify(batch, phaseTag, idx) {
  const written = await agent(writePrompt(batch), { phase: 'Write', label: `write:${phaseTag}:${idx}`, schema: RECORDS })
  if (!written || !written.records) return { records: [], rejected: batch.map(c => ({ candidate: c, reason: 'writer failed' })) }
  const inScope = written.records.filter(r => r.in_scope !== false)
  const rejected = written.records.filter(r => r.in_scope === false).map(r => ({ candidate: r.candidate_name, reason: r.notes }))
  if (!inScope.length) return { records: [], rejected }
  const [facts, scope] = await parallel(['facts', 'scope'].map(lens => () =>
    agent(verifyPrompt(lens, inScope), { phase: 'Verify', label: `${lens}:${phaseTag}:${idx}`, schema: VERDICTS })))
  const byId = (v) => Object.fromEntries(((v && v.verdicts) || []).map(x => [x.id, x]))
  const f = byId(facts), s = byId(scope)
  return { records: inScope.map(r => ({ record: r, facts: f[r.id] || null, scope: s[r.id] || null })), rejected }
}

// ---- Discover (barrier: dedupe needs every finder's output)
phase('Discover')
const raw = (await parallel(FINDERS.map(f => () =>
  agent(f.prompt, { phase: 'Discover', label: f.key, schema: CANDIDATES })))).filter(Boolean).flatMap(r => r.candidates || [])
log(`${C.era}: ${raw.length} raw candidates from ${FINDERS.length} finders`)

// ---- Dedupe per year
phase('Dedupe')
const years = {}
for (const c of raw) { const y = yearOf(c.date) || yearOf(C.from); (years[y] = years[y] || []).push(c) }
const deduped = (await parallel(Object.entries(years).map(([y, list]) => () => agent(
  `${RULES}\nDeduplicate these candidate ${CHAIN} incidents (year ${y}). Merge entries describing the same event (combine source URLs, keep the best date and loss estimate). Remove clearly out-of-scope items. Return the unique list.\nCandidates (JSON): ${JSON.stringify(list)}`,
  { phase: 'Dedupe', label: `dedupe:${y}`, schema: CANDIDATES })))).filter(Boolean).flatMap(r => r.candidates || [])
log(`${C.era}: ${deduped.length} unique candidates after dedupe`)

// ---- Write + verify, pipelined per batch
const results = []
const rejected = []
const batches = chunk(deduped, 6)
const out = await pipeline(batches, (b, _orig, i) => writeAndVerify(b, 'main', i))
for (const o of out.filter(Boolean)) { results.push(...o.records); rejected.push(...o.rejected) }
log(`${C.era}: ${results.length} records written and verified`)

// ---- Critic: loop until no notable incident is missing (max 2 rounds)
for (let round = 1; round <= 2; round++) {
  const have = results.map(r => `${r.record.date} ${r.record.project} (${r.record.loss_usd ?? '?'})`)
  const missing = await agent(
    `${RULES}\n${TOOLS}\nCompleteness check for the ${CHAIN} case library, era ${ERA}. Records already written:\n${have.join('\n')}\nAlready rejected: ${rejected.map(r => typeof r.candidate === 'string' ? r.candidate : (r.candidate && r.candidate.name)).filter(Boolean).join('; ')}\nSearch ${CATALOGS} for in-scope incidents in this era that are MISSING above (check the largest-loss lists first). Return up to 20 missing candidates; return an empty list if nothing notable is missing.`,
    { phase: 'Critic', label: `critic:r${round}`, schema: CANDIDATES })
  const fresh = (missing && missing.candidates) || []
  log(`${C.era}: critic round ${round} found ${fresh.length} missing`)
  if (!fresh.length) break
  const extra = await pipeline(chunk(fresh, 6), (b, _orig, i) => writeAndVerify(b, `critic${round}`, i))
  for (const o of extra.filter(Boolean)) { results.push(...o.records); rejected.push(...o.rejected) }
}

return { chain: C.chain, era: C.era, results, rejected }
