import React, { useMemo, useState } from 'react';
import type { ParcelProperties } from '../types/parcel';

// ──────────────────────────────────────────────────────────────────────
// DEFAULT ASSUMPTIONS — editable live in the "Adjust Assumptions" panel.
// Tweak the numeric defaults here to change baseline behavior.
// ──────────────────────────────────────────────────────────────────────

interface Assumptions {
  aduAllInCost: number;        // total project cost (hard + soft + sitework)
  aduSizeSqft: number;         // ADU size in SF (auto-pulled from feasibility)
  homeValue: number;           // subject home market value
  existingMortgageBalance: number; // outstanding mortgage on subject home
  rentPerSqftPerMonth: number; // rent comp; multiplied by aduSizeSqft / 12
  heloanLtvOnEquity: number;   // % of available equity that can be borrowed
  heloanRate: number;          // annual interest rate (decimal)
  heloanTermYears: number;
  propertyTaxIncreaseRate: number; // % of ADU cost annually
  insuranceAnnual: number;
  maintenancePctOfRent: number;
  vacancyPct: number;
  managementPct: number;       // 0 = self-managed
  rentGrowth: number;
  expenseGrowth: number;
  holdYears: number;
  exitCapRate: number;
  prefRate: number;
  ownerSplitAbovePref: number; // owner share above pref (0.7 = 70/30)
}

const DEFAULT_ASSUMPTIONS: Assumptions = {
  aduAllInCost: 250_000,
  aduSizeSqft: 525,
  homeValue: 750_000,
  existingMortgageBalance: 300_000,
  rentPerSqftPerMonth: 3.0,
  heloanLtvOnEquity: 0.80,
  heloanRate: 0.085,
  heloanTermYears: 20,
  propertyTaxIncreaseRate: 0.012,
  insuranceAnnual: 1_200,
  maintenancePctOfRent: 0.05,
  vacancyPct: 0.05,
  managementPct: 0.08,
  rentGrowth: 0.03,
  expenseGrowth: 0.025,
  holdYears: 10,
  exitCapRate: 0.065,
  prefRate: 0.08,
  ownerSplitAbovePref: 0.70,
};

// ──────────────────────────────────────────────────────────────────────
// FINANCIAL MATH — pure functions, no React deps.
// ──────────────────────────────────────────────────────────────────────

/** Standard amortizing loan payment, returns {monthlyPayment, annualDebtService}. */
function amortizingPayment(principal: number, annualRate: number, termYears: number) {
  if (principal <= 0) return { monthlyPayment: 0, annualDebtService: 0 };
  const i = annualRate / 12;
  const n = termYears * 12;
  if (i === 0) {
    const m = principal / n;
    return { monthlyPayment: m, annualDebtService: m * 12 };
  }
  const m = (principal * i) / (1 - Math.pow(1 + i, -n));
  return { monthlyPayment: m, annualDebtService: m * 12 };
}

/** Bisection-based IRR for a stream of cash flows starting at year 0. */
function irr(cashflows: number[], guess: number = 0.1): number | null {
  if (cashflows.length < 2) return null;
  const npv = (rate: number) =>
    cashflows.reduce((acc, cf, t) => acc + cf / Math.pow(1 + rate, t), 0);

  // Bisection over [-0.99, 5.0]
  let lo = -0.99;
  let hi = 5.0;
  let nLo = npv(lo);
  let nHi = npv(hi);
  if (nLo * nHi > 0) {
    // No sign change — try wider range or give up
    for (const r of [-0.95, -0.5, 0, 0.5, 1, 2, 5, 10]) {
      const n = npv(r);
      if (n * nLo < 0) {
        hi = r;
        nHi = n;
        break;
      }
    }
    if (npv(lo) * npv(hi) > 0) return null;
  }
  for (let iter = 0; iter < 100; iter++) {
    const mid = (lo + hi) / 2;
    const nMid = npv(mid);
    if (Math.abs(nMid) < 1e-4) return mid;
    if (nMid * nLo < 0) {
      hi = mid;
    } else {
      lo = mid;
      nLo = nMid;
    }
  }
  return (lo + hi) / 2;
}

interface YearRow {
  year: number;
  grossRent: number;
  vacancy: number;
  egi: number;
  opEx: number;
  noi: number;
  debtService: number;
  cfads: number;
  cumulativeCf: number;
}

interface ProFormaModel {
  assumptions: Assumptions;

  // Sources & Uses
  totalCost: number;
  availableEquity: number;
  heloanProceeds: number;
  ownerEquity: number;

  // Year 1 baseline (constants we use for growth)
  year1GrossRent: number;
  year1Vacancy: number;
  year1Egi: number;
  year1Tax: number;
  year1Insurance: number;
  year1Maintenance: number;
  year1Management: number;
  year1OpEx: number;
  year1Noi: number;
  annualDebtService: number;
  year1Cfads: number;

  // Time-series
  proForma: YearRow[];

  // Returns
  cashOnCashY1: number;
  cashOnCashY3: number;
  exitValue: number;
  unleveredIrr: number | null;
  leveredIrr: number | null;
  equityMultiple: number;
  breakEvenOccupancy: number;
  monthsToBreakEven: number | null;

  // Waterfall (illustrative JV)
  waterfall: WaterfallRow[];
}

interface WaterfallRow {
  year: number;
  cfads: number;
  prefAccrued: number;
  prefPaid: number;
  prefBalanceEnd: number;
  excess: number;
  ownerShare: number;
  partnerShare: number;
}

function buildProForma(a: Assumptions): ProFormaModel {
  // Sources & Uses
  const availableEquity = Math.max(0, a.homeValue - a.existingMortgageBalance);
  const heloanProceeds = Math.min(availableEquity * a.heloanLtvOnEquity, a.aduAllInCost);
  const ownerEquity = Math.max(0, a.aduAllInCost - heloanProceeds);

  // Debt service (level annual payment)
  const { annualDebtService } = amortizingPayment(heloanProceeds, a.heloanRate, a.heloanTermYears);

  // Year 1 baseline
  const year1GrossRent = a.rentPerSqftPerMonth * a.aduSizeSqft * 12;
  const year1Vacancy = year1GrossRent * a.vacancyPct;
  const year1Egi = year1GrossRent - year1Vacancy;
  const year1Tax = a.aduAllInCost * a.propertyTaxIncreaseRate;
  const year1Insurance = a.insuranceAnnual;
  const year1Maintenance = year1GrossRent * a.maintenancePctOfRent;
  const year1Management = year1Egi * a.managementPct;
  const year1OpEx = year1Tax + year1Insurance + year1Maintenance + year1Management;
  const year1Noi = year1Egi - year1OpEx;
  const year1Cfads = year1Noi - annualDebtService;

  // 10-year pro forma
  const proForma: YearRow[] = [];
  let cumulativeCf = 0;
  for (let yr = 1; yr <= a.holdYears; yr++) {
    const rentGrow = Math.pow(1 + a.rentGrowth, yr - 1);
    const expGrow = Math.pow(1 + a.expenseGrowth, yr - 1);

    const grossRent = year1GrossRent * rentGrow;
    const vacancy = grossRent * a.vacancyPct;
    const egi = grossRent - vacancy;

    const tax = year1Tax * expGrow;
    const insurance = year1Insurance * expGrow;
    const maintenance = grossRent * a.maintenancePctOfRent;
    const management = egi * a.managementPct;
    const opEx = tax + insurance + maintenance + management;
    const noi = egi - opEx;
    const cfads = noi - annualDebtService;
    cumulativeCf += cfads;

    proForma.push({
      year: yr,
      grossRent,
      vacancy,
      egi,
      opEx,
      noi,
      debtService: annualDebtService,
      cfads,
      cumulativeCf,
    });
  }

  // Exit value (terminal NOI / exit cap)
  const lastYear = proForma[proForma.length - 1];
  const terminalNoi = lastYear ? lastYear.noi * (1 + a.rentGrowth) : 0; // forward-NOI proxy
  const exitValue = terminalNoi > 0 ? terminalNoi / a.exitCapRate : 0;

  // IRRs — assume HELOAN balance at exit ~ remaining principal
  const remainingHeloanBalance = remainingPrincipal(
    heloanProceeds,
    a.heloanRate,
    a.heloanTermYears,
    a.holdYears
  );

  // Unlevered: spend full project cost at year 0, NOI each year, exit value at year 10
  const unleveredCf: number[] = [-a.aduAllInCost];
  for (let i = 0; i < proForma.length; i++) {
    let cf = proForma[i].noi;
    if (i === proForma.length - 1) cf += exitValue;
    unleveredCf.push(cf);
  }
  const unleveredIrr = irr(unleveredCf);

  // Levered: spend owner equity at year 0, CFADS each year, (exit value - remaining debt) at year 10
  const leveredCf: number[] = [-ownerEquity];
  for (let i = 0; i < proForma.length; i++) {
    let cf = proForma[i].cfads;
    if (i === proForma.length - 1) cf += exitValue - remainingHeloanBalance;
    leveredCf.push(cf);
  }
  const leveredIrr = irr(leveredCf);

  // Equity multiple = total levered distributions / equity invested
  const totalLeveredDistributions = leveredCf.slice(1).reduce((s, x) => s + x, 0);
  const equityMultiple = ownerEquity > 0 ? totalLeveredDistributions / ownerEquity : 0;

  // Returns ratios
  const cashOnCashY1 = ownerEquity > 0 ? year1Cfads / ownerEquity : 0;
  const y3 = proForma[2];
  const cashOnCashY3 = y3 && ownerEquity > 0 ? y3.cfads / ownerEquity : 0;

  // Break-even occupancy: occupancy% at which CFADS = 0 in Y1
  // grossRent*(occ) - opExNonRentVar - debtService = 0 → occ = (opExFixed + DS) / (grossRent - rent-var-opex)
  // simplification: solve for occupancy assuming rent-tied opex scales linearly
  const fixedOpex = year1Tax + year1Insurance;
  const variableOpexPerOccUnit = a.maintenancePctOfRent + a.managementPct * (1 - a.vacancyPct); // approx
  const denominator = year1GrossRent * (1 - variableOpexPerOccUnit);
  const breakEvenOccupancy =
    denominator > 0 ? Math.max(0, Math.min(1, (fixedOpex + annualDebtService) / denominator)) : 1;

  // Months to break-even: cumulative CFADS recovers owner equity
  let monthsToBreakEven: number | null = null;
  let runningEquityRecovery = 0;
  for (const row of proForma) {
    const monthly = row.cfads / 12;
    for (let m = 1; m <= 12; m++) {
      runningEquityRecovery += monthly;
      if (runningEquityRecovery >= ownerEquity) {
        monthsToBreakEven = (row.year - 1) * 12 + m;
        break;
      }
    }
    if (monthsToBreakEven != null) break;
  }

  // Waterfall — illustrative single-tier
  const waterfall: WaterfallRow[] = [];
  let prefBalance = ownerEquity;
  for (const row of proForma) {
    const prefAccrued = prefBalance * a.prefRate;
    const cfadsThisYear = Math.max(0, row.cfads);
    const prefPaid = Math.min(cfadsThisYear, prefAccrued);
    const excess = cfadsThisYear - prefPaid;
    const ownerShare = prefPaid + excess * a.ownerSplitAbovePref;
    const partnerShare = excess * (1 - a.ownerSplitAbovePref);
    prefBalance = prefBalance + prefAccrued - prefPaid;
    waterfall.push({
      year: row.year,
      cfads: row.cfads,
      prefAccrued,
      prefPaid,
      prefBalanceEnd: prefBalance,
      excess,
      ownerShare,
      partnerShare,
    });
  }

  return {
    assumptions: a,
    totalCost: a.aduAllInCost,
    availableEquity,
    heloanProceeds,
    ownerEquity,
    year1GrossRent,
    year1Vacancy,
    year1Egi,
    year1Tax,
    year1Insurance,
    year1Maintenance,
    year1Management,
    year1OpEx,
    year1Noi,
    annualDebtService,
    year1Cfads,
    proForma,
    cashOnCashY1,
    cashOnCashY3,
    exitValue,
    unleveredIrr,
    leveredIrr,
    equityMultiple,
    breakEvenOccupancy,
    monthsToBreakEven,
    waterfall,
  };
}

function remainingPrincipal(
  principal: number,
  annualRate: number,
  termYears: number,
  yearsElapsed: number
): number {
  if (principal <= 0) return 0;
  const i = annualRate / 12;
  const n = termYears * 12;
  const k = yearsElapsed * 12;
  if (k >= n) return 0;
  if (i === 0) return principal * (1 - k / n);
  const m = (principal * i) / (1 - Math.pow(1 + i, -n));
  // standard remaining balance formula
  const balance = (m * (1 - Math.pow(1 + i, -(n - k)))) / i;
  return balance;
}

// ──────────────────────────────────────────────────────────────────────
// FORMATTING HELPERS
// ──────────────────────────────────────────────────────────────────────

const fmtCurrency = (n: number, decimals = 0) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: decimals });
const fmtCurrencyK = (n: number) =>
  Math.abs(n) >= 1_000_000
    ? `$${(n / 1_000_000).toFixed(2)}M`
    : Math.abs(n) >= 1_000
    ? `$${(n / 1_000).toFixed(1)}K`
    : `$${n.toFixed(0)}`;
const fmtPct = (n: number, decimals = 1) =>
  Number.isFinite(n) ? `${(n * 100).toFixed(decimals)}%` : '—';
const fmtPctOrDash = (n: number | null | undefined, decimals = 1) =>
  n == null || !Number.isFinite(n) ? '—' : `${(n * 100).toFixed(decimals)}%`;

// Heatmap color: 0 = red, 1 = green
function heatColor(t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  // Interpolate red → amber → green
  if (clamped < 0.5) {
    const k = clamped * 2;
    const r = 224;
    const g = Math.round(60 + 140 * k);
    const b = 60;
    return `rgb(${r},${g},${b})`;
  } else {
    const k = (clamped - 0.5) * 2;
    const r = Math.round(224 - 130 * k);
    const g = 200;
    const b = Math.round(60 + 50 * k);
    return `rgb(${r},${g},${b})`;
  }
}

// ──────────────────────────────────────────────────────────────────────
// PROPS + COMPONENT
// ──────────────────────────────────────────────────────────────────────

interface Props {
  parcel: ParcelProperties;
  onClose: () => void;
}

function deriveAduSize(parcel: ParcelProperties): number {
  // Pull buildable area from the buildable_envelope rule's assumptions if available.
  const rules = parcel.rule_results || [];
  const benv = rules.find(r => r.rule_id === 'buildable_envelope');
  const ba = benv?.assumptions_used?.['buildable_area_sqft'];
  if (typeof ba === 'number' && ba >= 525) {
    // Cap at typical ADU max (900 sqft) and don't exceed the buildable envelope.
    return Math.min(900, ba);
  }
  // Fallback to Plinth standard 15×35 = 525 sqft
  return 525;
}

export const ProFormaPanel: React.FC<Props> = ({ parcel, onClose }) => {
  const [assumptions, setAssumptions] = useState<Assumptions>(() => ({
    ...DEFAULT_ASSUMPTIONS,
    aduSizeSqft: deriveAduSize(parcel),
  }));
  const [showAssumptions, setShowAssumptions] = useState(false);

  const model = useMemo(() => buildProForma(assumptions), [assumptions]);

  const upd = <K extends keyof Assumptions>(k: K, v: Assumptions[K]) =>
    setAssumptions(prev => ({ ...prev, [k]: v }));

  const compSet = useMemo(
    () => buildSyntheticCompSet(parcel, assumptions.aduSizeSqft, assumptions.rentPerSqftPerMonth),
    [parcel, assumptions.aduSizeSqft, assumptions.rentPerSqftPerMonth]
  );

  return (
    <div className="pf-overlay">
      <style>{PF_CSS}</style>

      <div className="pf-toolbar pf-no-print">
        <button className="pf-btn pf-btn-ghost" onClick={onClose}>← Close</button>
        <div className="pf-toolbar-title">Financial Pro Forma</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="pf-btn pf-btn-ghost"
            onClick={() => setShowAssumptions(s => !s)}
          >
            {showAssumptions ? 'Hide Assumptions' : 'Adjust Assumptions'}
          </button>
          <button className="pf-btn pf-btn-primary" onClick={() => window.print()}>
            ↓ Download PDF
          </button>
        </div>
      </div>

      {showAssumptions && (
        <div className="pf-assumptions pf-no-print">
          <h3>Assumptions (live editable)</h3>
          <div className="pf-grid">
            <NumField label="ADU All-In Cost" value={assumptions.aduAllInCost} onChange={v => upd('aduAllInCost', v)} prefix="$" />
            <NumField label="ADU Size (SF)" value={assumptions.aduSizeSqft} onChange={v => upd('aduSizeSqft', v)} />
            <NumField label="Subject Home Value" value={assumptions.homeValue} onChange={v => upd('homeValue', v)} prefix="$" />
            <NumField label="Existing Mortgage Balance" value={assumptions.existingMortgageBalance} onChange={v => upd('existingMortgageBalance', v)} prefix="$" />
            <NumField label="Rent ($/SF/mo)" value={assumptions.rentPerSqftPerMonth} onChange={v => upd('rentPerSqftPerMonth', v)} step={0.05} />
            <NumField label="HELOAN LTV on Equity" value={assumptions.heloanLtvOnEquity} onChange={v => upd('heloanLtvOnEquity', v)} step={0.05} pct />
            <NumField label="HELOAN Rate" value={assumptions.heloanRate} onChange={v => upd('heloanRate', v)} step={0.0025} pct />
            <NumField label="HELOAN Term (yrs)" value={assumptions.heloanTermYears} onChange={v => upd('heloanTermYears', v)} />
            <NumField label="Property Tax Rate (of ADU cost)" value={assumptions.propertyTaxIncreaseRate} onChange={v => upd('propertyTaxIncreaseRate', v)} step={0.001} pct />
            <NumField label="Insurance ($/yr)" value={assumptions.insuranceAnnual} onChange={v => upd('insuranceAnnual', v)} prefix="$" />
            <NumField label="Maintenance (% of rent)" value={assumptions.maintenancePctOfRent} onChange={v => upd('maintenancePctOfRent', v)} step={0.005} pct />
            <NumField label="Vacancy" value={assumptions.vacancyPct} onChange={v => upd('vacancyPct', v)} step={0.005} pct />
            <NumField label="Management (% of rent)" value={assumptions.managementPct} onChange={v => upd('managementPct', v)} step={0.005} pct />
            <NumField label="Rent Growth" value={assumptions.rentGrowth} onChange={v => upd('rentGrowth', v)} step={0.005} pct />
            <NumField label="Expense Growth" value={assumptions.expenseGrowth} onChange={v => upd('expenseGrowth', v)} step={0.005} pct />
            <NumField label="Hold Period (yrs)" value={assumptions.holdYears} onChange={v => upd('holdYears', v)} />
            <NumField label="Exit Cap Rate" value={assumptions.exitCapRate} onChange={v => upd('exitCapRate', v)} step={0.0025} pct />
            <NumField label="Pref Rate (waterfall)" value={assumptions.prefRate} onChange={v => upd('prefRate', v)} step={0.005} pct />
            <NumField label="Owner Split Above Pref" value={assumptions.ownerSplitAbovePref} onChange={v => upd('ownerSplitAbovePref', v)} step={0.05} pct />
          </div>
          <button className="pf-btn pf-btn-ghost" onClick={() => setAssumptions({ ...DEFAULT_ASSUMPTIONS, aduSizeSqft: deriveAduSize(parcel) })}>
            Reset to Defaults
          </button>
        </div>
      )}

      <div className="pf-doc">
        {/* Cover */}
        <header className="pf-cover">
          <div className="pf-eyebrow">PLINTH · ADU INVESTMENT MEMO</div>
          <h1>Financial Pro Forma</h1>
          <div className="pf-cover-meta">
            <div><span className="pf-label">Subject:</span> {parcel.address || parcel.parcel_id}</div>
            <div><span className="pf-label">Parcel ID:</span> {parcel.parcel_id}</div>
            <div><span className="pf-label">Municipality:</span> {(parcel.municipality_id || '').replace(/_/g, ' ').toUpperCase()}</div>
            <div><span className="pf-label">Feasibility Tier:</span> {parcel.tier ?? '—'} · Score {parcel.score?.toFixed(1) ?? '—'}</div>
            <div><span className="pf-label">ADU Size:</span> {assumptions.aduSizeSqft} SF</div>
            <div><span className="pf-label">Generated:</span> {new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}</div>
          </div>
        </header>

        {/* Section: Sources & Uses */}
        <section className="pf-section">
          <h2>Sources &amp; Uses</h2>
          <div className="pf-two-col">
            <table className="pf-table">
              <thead><tr><th>Uses</th><th className="num">Amount</th><th className="num">% of Total</th></tr></thead>
              <tbody>
                <tr><td>ADU All-In Construction</td><td className="num">{fmtCurrency(model.totalCost)}</td><td className="num">100.0%</td></tr>
                <tr className="pf-total"><td>Total Uses</td><td className="num">{fmtCurrency(model.totalCost)}</td><td className="num">100.0%</td></tr>
              </tbody>
            </table>
            <table className="pf-table">
              <thead><tr><th>Sources</th><th className="num">Amount</th><th className="num">% of Total</th></tr></thead>
              <tbody>
                <tr><td>HELOAN Proceeds (80% × available equity)</td><td className="num">{fmtCurrency(model.heloanProceeds)}</td><td className="num">{fmtPct(model.heloanProceeds / model.totalCost)}</td></tr>
                <tr><td>Owner Equity (gap)</td><td className="num">{fmtCurrency(model.ownerEquity)}</td><td className="num">{fmtPct(model.ownerEquity / model.totalCost)}</td></tr>
                <tr className="pf-total"><td>Total Sources</td><td className="num">{fmtCurrency(model.heloanProceeds + model.ownerEquity)}</td><td className="num">100.0%</td></tr>
              </tbody>
            </table>
          </div>
          <div className="pf-callout">
            <span className="pf-label">Available Equity:</span> {fmtCurrency(model.availableEquity)} (home value {fmtCurrencyK(assumptions.homeValue)} less mortgage {fmtCurrencyK(assumptions.existingMortgageBalance)}) ·
            HELOAN sized at {fmtPct(assumptions.heloanLtvOnEquity)} of available equity, capped at total project cost.
          </div>
        </section>

        {/* Section: Year 1 Operating Pro Forma */}
        <section className="pf-section">
          <h2>Year 1 Operating Pro Forma</h2>
          <table className="pf-table pf-y1">
            <tbody>
              <tr><td>Gross Potential Rent</td><td className="num">{fmtCurrency(model.year1GrossRent)}</td></tr>
              <tr><td className="indent">(Vacancy @ {fmtPct(assumptions.vacancyPct)})</td><td className="num neg">({fmtCurrency(model.year1Vacancy)})</td></tr>
              <tr className="pf-subtotal"><td>Effective Gross Income</td><td className="num">{fmtCurrency(model.year1Egi)}</td></tr>
              <tr><td className="indent">Property Tax ({fmtPct(assumptions.propertyTaxIncreaseRate)} of ADU cost)</td><td className="num neg">({fmtCurrency(model.year1Tax)})</td></tr>
              <tr><td className="indent">Insurance</td><td className="num neg">({fmtCurrency(model.year1Insurance)})</td></tr>
              <tr><td className="indent">Maintenance Reserve ({fmtPct(assumptions.maintenancePctOfRent)} of GPR)</td><td className="num neg">({fmtCurrency(model.year1Maintenance)})</td></tr>
              <tr><td className="indent">Property Management ({fmtPct(assumptions.managementPct)} of EGI)</td><td className="num neg">({fmtCurrency(model.year1Management)})</td></tr>
              <tr className="pf-subtotal"><td>Total Operating Expenses</td><td className="num neg">({fmtCurrency(model.year1OpEx)})</td></tr>
              <tr className="pf-total"><td>Net Operating Income</td><td className="num">{fmtCurrency(model.year1Noi)}</td></tr>
              <tr><td className="indent">(Debt Service — HELOAN)</td><td className="num neg">({fmtCurrency(model.annualDebtService)})</td></tr>
              <tr className="pf-total pf-final"><td>Cash Flow After Debt Service</td><td className="num">{fmtCurrency(model.year1Cfads)}</td></tr>
            </tbody>
          </table>
        </section>

        {/* Section: 10-Year Pro Forma */}
        <section className="pf-section">
          <h2>{assumptions.holdYears}-Year Pro Forma</h2>
          <div className="pf-scroll">
            <table className="pf-table pf-grid-table">
              <thead>
                <tr>
                  <th></th>
                  {model.proForma.map(r => <th key={r.year} className="num">Y{r.year}</th>)}
                </tr>
              </thead>
              <tbody>
                <FlowRow label="Gross Rent" rows={model.proForma} field="grossRent" />
                <FlowRow label="(Vacancy)" rows={model.proForma} field="vacancy" negative />
                <FlowRow label="EGI" rows={model.proForma} field="egi" subtotal />
                <FlowRow label="(OpEx)" rows={model.proForma} field="opEx" negative />
                <FlowRow label="NOI" rows={model.proForma} field="noi" subtotal />
                <FlowRow label="(Debt Service)" rows={model.proForma} field="debtService" negative />
                <FlowRow label="CFADS" rows={model.proForma} field="cfads" total />
                <FlowRow label="Cumulative CF" rows={model.proForma} field="cumulativeCf" />
              </tbody>
            </table>
          </div>
        </section>

        {/* Section: Returns Summary */}
        <section className="pf-section">
          <h2>Returns Summary</h2>
          <div className="pf-kpi-grid">
            <Kpi label="Y1 Cash-on-Cash" value={fmtPctOrDash(model.cashOnCashY1)} accent={model.cashOnCashY1 >= 0.05} />
            <Kpi label="Y3 Cash-on-Cash (Stabilized)" value={fmtPctOrDash(model.cashOnCashY3)} accent={model.cashOnCashY3 >= 0.06} />
            <Kpi label="Unlevered IRR" value={fmtPctOrDash(model.unleveredIrr)} accent={(model.unleveredIrr ?? 0) >= 0.07} />
            <Kpi label="Levered IRR" value={fmtPctOrDash(model.leveredIrr)} accent={(model.leveredIrr ?? 0) >= 0.12} />
            <Kpi label="Equity Multiple" value={`${model.equityMultiple.toFixed(2)}x`} accent={model.equityMultiple >= 1.8} />
            <Kpi label="Break-Even Occupancy" value={fmtPctOrDash(model.breakEvenOccupancy)} accent={model.breakEvenOccupancy <= 0.85} />
            <Kpi label="Months to Equity Break-Even" value={model.monthsToBreakEven != null ? `${model.monthsToBreakEven} mo` : '—'} accent={(model.monthsToBreakEven ?? 999) <= 96} />
            <Kpi label={`Exit Value @ ${(assumptions.exitCapRate * 100).toFixed(2)}% Cap`} value={fmtCurrency(model.exitValue)} />
          </div>
        </section>

        {/* Section: Rent Comp Set */}
        <section className="pf-section">
          <h2>Rent Comp Set</h2>
          <p className="pf-fineprint">
            {compSet.note}
          </p>
          <table className="pf-table">
            <thead>
              <tr>
                <th>Listing</th><th className="num">Size (SF)</th><th className="num">Bd</th><th className="num">$/mo</th><th className="num">$/SF/mo</th>
              </tr>
            </thead>
            <tbody>
              {compSet.comps.map((c, i) => (
                <tr key={i}>
                  <td>{c.label}</td>
                  <td className="num">{c.sizeSqft}</td>
                  <td className="num">{c.beds}</td>
                  <td className="num">{fmtCurrency(c.rentMonthly)}</td>
                  <td className="num">${c.rentMonthly / c.sizeSqft >= 0 ? (c.rentMonthly / c.sizeSqft).toFixed(2) : '—'}</td>
                </tr>
              ))}
              <tr className="pf-total">
                <td>Median (used in pro forma)</td>
                <td className="num">{assumptions.aduSizeSqft}</td>
                <td className="num">—</td>
                <td className="num">{fmtCurrency(assumptions.rentPerSqftPerMonth * assumptions.aduSizeSqft)}</td>
                <td className="num">${assumptions.rentPerSqftPerMonth.toFixed(2)}</td>
              </tr>
            </tbody>
          </table>
        </section>

        {/* Section: Equity Waterfall */}
        <section className="pf-section">
          <h2>Illustrative Waterfall <span className="pf-eyebrow-inline">(Hypothetical JV Structure)</span></h2>
          <p className="pf-fineprint">
            {fmtPct(assumptions.prefRate)} preferred return on owner equity, then{' '}
            {fmtPct(assumptions.ownerSplitAbovePref)} / {fmtPct(1 - assumptions.ownerSplitAbovePref)} owner / partner above pref.
            Distributions assume non-negative annual CFADS only.
          </p>
          <div className="pf-scroll">
            <table className="pf-table pf-grid-table">
              <thead>
                <tr>
                  <th></th>
                  {model.waterfall.map(r => <th key={r.year} className="num">Y{r.year}</th>)}
                </tr>
              </thead>
              <tbody>
                <WfRow label="CFADS" rows={model.waterfall} field="cfads" />
                <WfRow label="Pref Accrued" rows={model.waterfall} field="prefAccrued" />
                <WfRow label="Pref Paid" rows={model.waterfall} field="prefPaid" />
                <WfRow label="Pref Balance EOY" rows={model.waterfall} field="prefBalanceEnd" />
                <WfRow label="Excess (above pref)" rows={model.waterfall} field="excess" />
                <WfRow label="Owner Distribution" rows={model.waterfall} field="ownerShare" total />
                <WfRow label="Partner Distribution" rows={model.waterfall} field="partnerShare" />
              </tbody>
            </table>
          </div>
        </section>

        {/* Section: Sensitivity */}
        <section className="pf-section">
          <h2>Sensitivity — Levered IRR</h2>
          <p className="pf-fineprint">Rows: ADU all-in cost · Columns: rent ($/mo). All other assumptions held constant.</p>
          <SensitivityTable assumptions={assumptions} />
        </section>

        <footer className="pf-footer">
          <div>
            This memo is illustrative and based on user-editable assumptions. It is not investment advice and should not
            be relied upon for capital deployment decisions. Verify all inputs — including comp rents, financing terms,
            and tax assumptions — against current market and lender data.
          </div>
          <div className="pf-footer-mark">PLINTH · ADU INVESTMENT MEMO</div>
        </footer>
      </div>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────────────
// SUB-COMPONENTS
// ──────────────────────────────────────────────────────────────────────

interface NumFieldProps {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  prefix?: string;
  pct?: boolean;
}
const NumField: React.FC<NumFieldProps> = ({ label, value, onChange, step, prefix, pct }) => {
  const display = pct ? (value * 100).toFixed(2) : String(value);
  return (
    <label className="pf-field">
      <span className="pf-field-label">{label}</span>
      <span className="pf-field-input">
        {prefix && <span className="pf-prefix">{prefix}</span>}
        <input
          type="number"
          step={step ?? 1}
          value={display}
          onChange={e => {
            const v = parseFloat(e.target.value);
            if (Number.isNaN(v)) return;
            onChange(pct ? v / 100 : v);
          }}
        />
        {pct && <span className="pf-suffix">%</span>}
      </span>
    </label>
  );
};

const Kpi: React.FC<{ label: string; value: string; accent?: boolean }> = ({ label, value, accent }) => (
  <div className={`pf-kpi ${accent ? 'pf-kpi-accent' : ''}`}>
    <div className="pf-kpi-label">{label}</div>
    <div className="pf-kpi-value">{value}</div>
  </div>
);

const FlowRow: React.FC<{
  label: string;
  rows: YearRow[];
  field: keyof YearRow;
  negative?: boolean;
  subtotal?: boolean;
  total?: boolean;
}> = ({ label, rows, field, negative, subtotal, total }) => (
  <tr className={subtotal ? 'pf-subtotal' : total ? 'pf-total' : ''}>
    <td>{label}</td>
    {rows.map(r => {
      const v = r[field] as number;
      return (
        <td key={r.year} className={`num ${negative ? 'neg' : ''}`}>
          {negative ? `(${fmtCurrencyK(v)})` : fmtCurrencyK(v)}
        </td>
      );
    })}
  </tr>
);

const WfRow: React.FC<{
  label: string;
  rows: WaterfallRow[];
  field: keyof WaterfallRow;
  total?: boolean;
}> = ({ label, rows, field, total }) => (
  <tr className={total ? 'pf-total' : ''}>
    <td>{label}</td>
    {rows.map(r => (
      <td key={r.year} className="num">{fmtCurrencyK(r[field] as number)}</td>
    ))}
  </tr>
);

const SensitivityTable: React.FC<{ assumptions: Assumptions }> = ({ assumptions }) => {
  // Center on current cost & rent; ±20% bands
  const baseCost = assumptions.aduAllInCost;
  const baseMonthlyRent = assumptions.rentPerSqftPerMonth * assumptions.aduSizeSqft;

  const costSteps = [-0.2, -0.1, 0, 0.1, 0.2].map(d => Math.round(baseCost * (1 + d) / 1000) * 1000);
  const rentSteps = [-0.2, -0.1, 0, 0.1, 0.2].map(d => Math.round(baseMonthlyRent * (1 + d) / 25) * 25);

  // Compute IRR grid
  const grid = costSteps.map(cost =>
    rentSteps.map(rent => {
      const a: Assumptions = {
        ...assumptions,
        aduAllInCost: cost,
        rentPerSqftPerMonth: rent / assumptions.aduSizeSqft,
      };
      const m = buildProForma(a);
      return m.leveredIrr ?? 0;
    })
  );

  const allVals = grid.flat();
  const lo = Math.min(...allVals);
  const hi = Math.max(...allVals);

  return (
    <div className="pf-scroll">
      <table className="pf-table pf-sens">
        <thead>
          <tr>
            <th>ADU Cost \ Rent/mo</th>
            {rentSteps.map(r => <th key={r} className="num">{fmtCurrency(r)}</th>)}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, i) => (
            <tr key={costSteps[i]}>
              <td>{fmtCurrencyK(costSteps[i])}</td>
              {row.map((v, j) => {
                const t = hi > lo ? (v - lo) / (hi - lo) : 0.5;
                return (
                  <td key={rentSteps[j]} className="num pf-sens-cell" style={{ background: heatColor(t) }}>
                    {fmtPctOrDash(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────────────
// SYNTHETIC COMP SET (no live API wired)
// ──────────────────────────────────────────────────────────────────────

interface CompListing {
  label: string;
  sizeSqft: number;
  beds: string;
  rentMonthly: number;
}

function buildSyntheticCompSet(
  parcel: ParcelProperties,
  aduSizeSqft: number,
  rentPerSqftPerMonth: number
): { comps: CompListing[]; note: string } {
  const muniLabel = (parcel.municipality_id || '').replace(/_/g, ' ').toUpperCase();
  // Generate 5 comps at slightly different sizes & rents around the median
  const seedSize = aduSizeSqft;
  const seedRent = rentPerSqftPerMonth;
  const variants = [
    { dSize: -50, dRent: -0.1, beds: 'Studio' },
    { dSize: -25, dRent: -0.05, beds: '1 BR' },
    { dSize: 0, dRent: 0.0, beds: '1 BR' },
    { dSize: 50, dRent: 0.05, beds: '1 BR' },
    { dSize: 100, dRent: 0.1, beds: '1 BR' },
  ];
  const comps: CompListing[] = variants.map((v, i) => {
    const sz = Math.max(400, seedSize + v.dSize);
    const rentSf = seedRent * (1 + v.dRent);
    return {
      label: `${muniLabel} comp #${i + 1}`,
      sizeSqft: sz,
      beds: v.beds,
      rentMonthly: Math.round(rentSf * sz / 5) * 5,
    };
  });
  return {
    comps,
    note:
      'No external rent-comp API is currently wired. The comp set below is a synthetic band ' +
      'centered on the editable $/SF/month assumption — meant for illustration. Replace with ' +
      'live Zillow/RentCast/Compass data once an API key is configured.',
  };
}

// ──────────────────────────────────────────────────────────────────────
// STYLES — scoped to .pf-* classes; print stylesheet collapses overlay.
// ──────────────────────────────────────────────────────────────────────

const PF_CSS = `
.pf-overlay {
  position: fixed; inset: 0; z-index: 5000;
  background: #0c0c0c;
  color: #f0f0f0;
  font-family: 'Syne', 'Inter', system-ui, -apple-system, sans-serif;
  overflow: auto;
  padding-bottom: 60px;
}
.pf-toolbar {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 28px; background: #141414; border-bottom: 1px solid #2a2a2a;
}
.pf-toolbar-title {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 18px; letter-spacing: 0.04em; color: #f0f0f0;
}
.pf-btn {
  padding: 8px 16px; border-radius: 4px; font-size: 12px;
  font-weight: 600; letter-spacing: 0.04em; cursor: pointer;
  border: 1px solid transparent; background: none;
  color: #f0f0f0;
  transition: background 0.15s, border-color 0.15s;
  font-family: inherit;
}
.pf-btn-ghost { border-color: #2a2a2a; color: #cccccc; }
.pf-btn-ghost:hover { border-color: #5de0a0; color: #5de0a0; }
.pf-btn-primary { background: #0d2a1a; border-color: #5de0a0; color: #5de0a0; }
.pf-btn-primary:hover { background: #14422a; }

.pf-assumptions {
  margin: 14px 28px; padding: 18px; background: #131313;
  border: 1px solid #2a2a2a; border-radius: 6px;
}
.pf-assumptions h3 {
  margin: 0 0 12px; font-family: 'Playfair Display', Georgia, serif;
  font-size: 14px; letter-spacing: 0.06em; text-transform: uppercase; color: #888;
}
.pf-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 10px 16px; margin-bottom: 12px;
}
@media (max-width: 1100px) { .pf-grid { grid-template-columns: repeat(2, 1fr); } }
.pf-field { display: flex; flex-direction: column; gap: 4px; }
.pf-field-label { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.06em; }
.pf-field-input {
  display: flex; align-items: center; background: #0a0a0a;
  border: 1px solid #2a2a2a; border-radius: 4px; padding: 4px 8px;
}
.pf-field-input:focus-within { border-color: #5de0a0; }
.pf-field-input input {
  flex: 1; background: transparent; border: none; color: #f0f0f0;
  font-family: inherit; font-size: 13px; outline: none; padding: 4px 0;
  min-width: 0;
}
.pf-prefix, .pf-suffix { color: #888; font-size: 12px; }

.pf-doc {
  max-width: 1100px; margin: 24px auto; padding: 0 32px;
}

.pf-cover {
  border-bottom: 1px solid #2a2a2a; padding-bottom: 28px; margin-bottom: 28px;
}
.pf-eyebrow {
  font-size: 11px; letter-spacing: 0.18em; color: #5de0a0;
  text-transform: uppercase; margin-bottom: 14px;
}
.pf-eyebrow-inline {
  font-size: 11px; letter-spacing: 0.12em; color: #888;
  text-transform: uppercase; font-weight: 400;
}
.pf-cover h1 {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 42px; line-height: 1.1; margin: 0 0 24px; color: #f0f0f0;
  font-weight: 600;
}
.pf-cover-meta {
  display: grid; grid-template-columns: repeat(2, 1fr);
  gap: 8px 32px; font-size: 12px; color: #cccccc;
}
.pf-label { color: #888; letter-spacing: 0.04em; }

.pf-section {
  margin-bottom: 36px; page-break-inside: avoid;
}
.pf-section h2 {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 22px; color: #f0f0f0; margin: 0 0 16px;
  border-bottom: 1px solid #2a2a2a; padding-bottom: 8px;
  font-weight: 500;
}
.pf-fineprint {
  font-size: 11px; color: #888; line-height: 1.5; margin: 0 0 12px;
}
.pf-callout {
  background: #131313; border-left: 3px solid #5de0a0;
  padding: 10px 14px; margin-top: 14px; font-size: 12px; color: #ccc;
  line-height: 1.5;
}

.pf-two-col {
  display: grid; grid-template-columns: 1fr 1fr; gap: 24px;
}
@media (max-width: 800px) { .pf-two-col { grid-template-columns: 1fr; } }

.pf-table {
  width: 100%; border-collapse: collapse;
  font-size: 12px; color: #cccccc;
}
.pf-table th, .pf-table td {
  padding: 8px 10px; text-align: left;
  border-bottom: 1px solid #1f1f1f;
}
.pf-table th {
  color: #888; font-weight: 500; text-transform: uppercase;
  letter-spacing: 0.06em; font-size: 10px; background: #131313;
}
.pf-table td.num, .pf-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pf-table td.indent { padding-left: 22px; color: #aaa; }
.pf-table td.neg { color: #d0a4a4; }
.pf-subtotal td { background: #131313; font-weight: 500; color: #f0f0f0; }
.pf-total td {
  background: #181818; font-weight: 700; color: #f0f0f0;
  border-top: 1px solid #2a2a2a;
}
.pf-final td { color: #5de0a0; }

.pf-y1 { max-width: 600px; }
.pf-y1 td:first-child { width: 75%; }

.pf-grid-table th:first-child, .pf-grid-table td:first-child {
  position: sticky; left: 0; background: inherit;
  font-weight: 500; color: #cccccc;
}
.pf-grid-table thead th { background: #131313; }
.pf-grid-table { font-size: 11px; }
.pf-grid-table td, .pf-grid-table th { padding: 6px 8px; }

.pf-scroll { overflow-x: auto; }

.pf-kpi-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
@media (max-width: 900px) { .pf-kpi-grid { grid-template-columns: repeat(2, 1fr); } }
.pf-kpi {
  padding: 16px; background: #131313; border: 1px solid #2a2a2a;
  border-radius: 4px;
}
.pf-kpi-accent { border-color: #5de0a0; }
.pf-kpi-label {
  font-size: 10px; color: #888; text-transform: uppercase;
  letter-spacing: 0.08em; margin-bottom: 8px;
}
.pf-kpi-value {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 22px; color: #f0f0f0; font-weight: 500;
}
.pf-kpi-accent .pf-kpi-value { color: #5de0a0; }

.pf-sens td.pf-sens-cell {
  color: #0c0c0c; font-weight: 700;
}

.pf-footer {
  margin-top: 60px; padding-top: 20px;
  border-top: 1px solid #2a2a2a;
  font-size: 10px; color: #666; line-height: 1.6;
  display: flex; justify-content: space-between; gap: 30px;
}
.pf-footer-mark {
  letter-spacing: 0.16em; color: #5de0a0; font-weight: 600;
  white-space: nowrap;
}

@media print {
  .pf-no-print { display: none !important; }
  .pf-overlay {
    position: static !important;
    background: #fff !important;
    color: #111 !important;
  }
  .pf-doc { max-width: none; padding: 0; margin: 0; }
  .pf-cover h1, .pf-section h2, .pf-kpi-value { color: #111 !important; }
  .pf-eyebrow, .pf-footer-mark, .pf-final td, .pf-kpi-accent .pf-kpi-value { color: #1c6e44 !important; }
  .pf-table { color: #222 !important; }
  .pf-table th { color: #555 !important; background: #f4f4f4 !important; }
  .pf-table td.neg { color: #8a3a3a !important; }
  .pf-table td, .pf-table th { border-bottom: 1px solid #d6d6d6 !important; }
  .pf-subtotal td { background: #f4f4f4 !important; color: #111 !important; }
  .pf-total td { background: #ececec !important; color: #111 !important; border-top: 1px solid #999 !important; }
  .pf-callout { background: #f4f4f4 !important; color: #333 !important; }
  .pf-kpi { background: #fafafa !important; border-color: #ccc !important; }
  .pf-kpi-label { color: #666 !important; }
  .pf-kpi-accent { border-color: #1c6e44 !important; }
  .pf-fineprint, .pf-footer { color: #555 !important; }
  .pf-section { page-break-inside: avoid; }
}
`;
