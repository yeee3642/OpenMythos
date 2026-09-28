export const meta = {
  name: 'dhl-case-records',
  description: 'Turn DeFiHackLabs Ethereum/BSC incidents (with PoC code) into verified case-library records, no web search needed',
  phases: [
    { title: 'Write', detail: '10 incidents per agent, grounded in the PoC code' },
    { title: 'Verify', detail: 'independent re-read of PoC and entry per batch' },
  ],
}

const C = args
const RULES = `
You are writing records for the ${C.chainName} security-incident case library (read by Taiwan users).
SOURCE MATERIAL ONLY: each incident's DeFiHackLabs entry (name, date, root-cause label, "lost" text, parsed USD, candidate sources) and its exploit proof-of-concept Solidity file at poc_path (read it with the Read tool; the header comments and test code show the vulnerable contract, attack steps and often a Total Lost line). Do NOT use WebSearch or WebFetch (the search budget is exhausted). Do not invent facts, amounts, attackers or URLs. You may add widely documented context for famous incidents (e.g. well-known attribution) only if you are certain, otherwise say 'Unknown'.
Fields (all required):
- id: kebab-case of the project name + '-' + YYYY-MM of the date (e.g. "snk-2023-05").
- date: the entry's date (YYYY-MM-DD).
- project: project name.
- project_type: bridge|lending|dex_amm|perp_derivatives|stablecoin|yield_vault|wallet|cex|trading_bot|nft_gaming|launchpad|dao_governance|infrastructure_sdk|l1_runtime|staking|other (many BSC entries are single tokens with custom transfer/fee/reward logic -> "other").
- category: smart_contract_bug | oracle_price_manipulation (spot-price / oracle manipulation, usually flash-loan funded) | economic_exploit (flash-loan or economic design abuse where code works as written, arbitrage of broken tokenomics) | private_key_compromise | insider_threat | governance_attack | phishing_social_engineering | supply_chain | frontend_account_compromise | third_party_compromise | operational_error.
- vuln_pattern: REQUIRED iff category is smart_contract_bug, else null. One of: reentrancy, access_control (missing permission checks, unprotected init/mint/withdraw), input_validation (unvalidated input or arbitrary external call, e.g. draining users' approvals), signature_message_verification (signature replay, ecrecover zero address, forged cross-chain messages), arithmetic (overflow, rounding, precision loss, share inflation / donation / empty-market attacks), business_logic (flawed reward/fee/accounting/state logic, e.g. fee-on-transfer, skim/sync, reflection bugs), proxy_upgrade (uninitialized implementation, re-initialization, storage collision), compiler_bug, crypto_verification, offchain_logic, undisclosed.
- attack_vector: short English phrase (you may reuse the DeFiHackLabs root-cause label).
- loss_usd: if loss_usd_parsed is present use it exactly; else if the lost text is in ETH/WETH/BNB/WBNB/stablecoins estimate USD using the approximate market price on that date and state the price used in notes; else null.
- assets_stolen: the lost text, lightly cleaned.
- recovered_usd: null unless the source material states a recovery; recovery_status: "unknown" unless stated ("returned_by_attacker", "partial", etc.); never "not_applicable" when loss > 0.
- attribution: 'Unknown' unless stated or famously documented.
- chain_scope: "${C.singleScope}".
- summary_zh (2-3 sentences), root_cause_zh (1-2 sentences explaining the actual bug from the PoC), aftermath_zh (1 sentence; if unknown write 「資料來源未記載資金追回或後續處理。」): Traditional Chinese, Taiwan usage, NO Simplified characters.
- confidence: "medium" by default; "high" only for famous incidents whose facts you are certain of; "low" if the PoC is unclear.
- notes: English; say the record is based on the DeFiHackLabs entry and PoC, and any USD estimate method.
- sources: choose 1-3 ONLY from the incident's candidate_sources (always include the DeFiHackLabs PoC entry).
- in_scope: false for pure rug pulls / exit scams / deployer backdoors with no exploit of a third party, or if the PoC shows the incident is not on ${C.chainName}; true otherwise.
- candidate_name: the entry's name.
`
const SOURCE = { type: 'object', properties: { title: { type: 'string' }, publisher: { type: 'string' }, url: { type: 'string' } }, required: ['title', 'publisher', 'url'] }
const FIELDS = ['id','date','project','project_type','category','vuln_pattern','attack_vector','loss_usd','assets_stolen','recovered_usd','recovery_status','attribution','chain_scope','summary_zh','root_cause_zh','aftermath_zh','confidence','notes','sources']
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
  required: FIELDS.concat(['in_scope', 'candidate_name']),
}
const RECORDS = { type: 'object', properties: { records: { type: 'array', items: RECORD } }, required: ['records'] }
const VERDICTS = {
  type: 'object',
  properties: { verdicts: { type: 'array', items: { type: 'object', properties: {
    id: { type: 'string' }, verdict: { type: 'string', enum: ['keep', 'fix', 'drop'] }, reasons: { type: 'string' },
    corrections: { type: 'object', additionalProperties: true },
  }, required: ['id', 'verdict', 'reasons'] } } },
  required: ['verdicts'],
}

const BATCH = 10
const batches = []
for (let i = 0; i < C.count; i += BATCH) batches.push([i, Math.min(i + BATCH, C.count) - 1])

const results = await pipeline(
  batches,
  ([a, b], _o, idx) => agent(
    `${RULES}\nInput: the JSON array in ${C.inputPath}. Handle the entries at array indexes ${a} through ${b} inclusive (0-based). For each one, Read its poc_path file (at least the first 150 lines) before writing. Return one record per entry, in order.`,
    { phase: 'Write', label: `write:${C.part}:${a}-${b}`, schema: RECORDS }),
  async (written, [a, b], idx) => {
    const recs = (written && written.records) || []
    if (!recs.length) return { results: [], rejected: [{ batch: [a, b], reason: 'writer failed' }] }
    const inScope = recs.filter(r => r.in_scope !== false)
    const rejected = recs.filter(r => r.in_scope === false).map(r => ({ candidate: r.candidate_name, reason: r.notes }))
    if (!inScope.length) return { results: [], rejected }
    const v = await agent(
      `${RULES}\nYou are the independent REVIEWER. Records written by another agent (JSON): ${JSON.stringify(inScope)}\nThe matching source entries are at indexes ${a} through ${b} of ${C.inputPath}; re-read each entry and its poc_path file yourself.\nTry to REFUTE each record: does summary_zh / root_cause_zh match what the PoC actually does? Is category / vuln_pattern right per the rules (vuln_pattern required iff smart_contract_bug)? Does loss_usd equal loss_usd_parsed when present, and is any estimate reasonable and explained in notes? Are all sources taken from candidate_sources? Is the Chinese Traditional (Taiwan) with no Simplified characters? Is it really in scope (not a pure rug pull / deployer backdoor)?\nReturn one verdict per record id: keep; fix with ONLY the corrected fields in corrections (any record field may be corrected; sources replaces the whole list); drop if out of scope.`,
      { phase: 'Verify', label: `verify:${C.part}:${a}-${b}`, schema: VERDICTS })
    const byId = Object.fromEntries(((v && v.verdicts) || []).map(x => [x.id, x]))
    // The single reviewer covers both facts and classification.
    return { results: inScope.map(r => ({ record: r, facts: byId[r.id] || null, scope: byId[r.id] || null })), rejected }
  },
)
const flat = results.filter(Boolean)
const out = { results: flat.flatMap(r => r.results), rejected: flat.flatMap(r => r.rejected) }
log(`${C.part}: ${out.results.length} records, ${out.rejected.length} rejected`)
return out
