import React, { useMemo, useState } from 'react';
import type { RentSpecEstimate } from '../api/client';
import {
  ASSUMPTIONS as APP_ASSUMPTIONS,
  MONTH_LABELS,
  seasonalAnnualRevenue,
  type PlinthModel,
} from '../config';

// ──────────────────────────────────────────────────────────────────────
// DEFAULT ASSUMPTIONS — editable live in the "Adjust Assumptions" panel.
// ──────────────────────────────────────────────────────────────────────

interface Assumptions {
  aduAllInCost: number;
  aduSizeSqft: number;
  homeValue: number;
  existingMortgageBalance: number;
  rentPerSqftPerMonth: number;
  heloanLtvOnEquity: number;
  heloanRate: number;
  heloanTermYears: number;
  propertyTaxIncreaseRate: number;
  insuranceAnnual: number;
  maintenancePctOfRent: number;
  vacancyPct: number;
  managementPct: number;
  rentGrowth: number;
  expenseGrowth: number;
  holdYears: number;
  exitCapRate: number;
  prefRate: number;
  ownerSplitAbovePref: number;
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

function irr(cashflows: number[]): number | null {
  if (cashflows.length < 2) return null;
  const npv = (rate: number) =>
    cashflows.reduce((acc, cf, t) => acc + cf / Math.pow(1 + rate, t), 0);

  let lo = -0.99;
  let hi = 5.0;
  let nLo = npv(lo);
  let nHi = npv(hi);
  if (nLo * nHi > 0) {
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

interface ProFormaModel {
  assumptions: Assumptions;
  totalCost: number;
  availableEquity: number;
  heloanProceeds: number;
  ownerEquity: number;
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
  proForma: YearRow[];
  cashOnCashY1: number;
  cashOnCashY3: number;
  exitValue: number;
  unleveredIrr: number | null;
  leveredIrr: number | null;
  equityMultiple: number;
  breakEvenOccupancy: number;
  monthsToBreakEven: number | null;
  waterfall: WaterfallRow[];
}

function buildProForma(a: Assumptions): ProFormaModel {
  const availableEquity = Math.max(0, a.homeValue - a.existingMortgageBalance);
  const heloanProceeds = Math.min(availableEquity * a.heloanLtvOnEquity, a.aduAllInCost);
  const ownerEquity = Math.max(0, a.aduAllInCost - heloanProceeds);

  const { annualDebtService } = amortizingPayment(heloanProceeds, a.heloanRate, a.heloanTermYears);

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
      year: yr, grossRent, vacancy, egi, opEx, noi,
      debtService: annualDebtService, cfads, cumulativeCf,
    });
  }

  const lastYear = proForma[proForma.length - 1];
  const terminalNoi = lastYear ? lastYear.noi * (1 + a.rentGrowth) : 0;
  const exitValue = terminalNoi > 0 ? terminalNoi / a.exitCapRate : 0;

  const remainingHeloanBalance = remainingPrincipal(
    heloanProceeds, a.heloanRate, a.heloanTermYears, a.holdYears,
  );

  const unleveredCf: number[] = [-a.aduAllInCost];
  for (let i = 0; i < proForma.length; i++) {
    let cf = proForma[i].noi;
    if (i === proForma.length - 1) cf += exitValue;
    unleveredCf.push(cf);
  }
  const unleveredIrr = irr(unleveredCf);

  const leveredCf: number[] = [-ownerEquity];
  for (let i = 0; i < proForma.length; i++) {
    let cf = proForma[i].cfads;
    if (i === proForma.length - 1) cf += exitValue - remainingHeloanBalance;
    leveredCf.push(cf);
  }
  const leveredIrr = irr(leveredCf);

  const totalLeveredDistributions = leveredCf.slice(1).reduce((s, x) => s + x, 0);
  const equityMultiple = ownerEquity > 0 ? totalLeveredDistributions / ownerEquity : 0;

  const cashOnCashY1 = ownerEquity > 0 ? year1Cfads / ownerEquity : 0;
  const y3 = proForma[2];
  const cashOnCashY3 = y3 && ownerEquity > 0 ? y3.cfads / ownerEquity : 0;

  const fixedOpex = year1Tax + year1Insurance;
  const variableOpexPerOccUnit = a.maintenancePctOfRent + a.managementPct * (1 - a.vacancyPct);
  const denominator = year1GrossRent * (1 - variableOpexPerOccUnit);
  const breakEvenOccupancy =
    denominator > 0 ? Math.max(0, Math.min(1, (fixedOpex + annualDebtService) / denominator)) : 1;

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
      year: row.year, cfads: row.cfads,
      prefAccrued, prefPaid, prefBalanceEnd: prefBalance,
      excess, ownerShare, partnerShare,
    });
  }

  return {
    assumptions: a,
    totalCost: a.aduAllInCost,
    availableEquity, heloanProceeds, ownerEquity,
    year1GrossRent, year1Vacancy, year1Egi,
    year1Tax, year1Insurance, year1Maintenance, year1Management,
    year1OpEx, year1Noi, annualDebtService, year1Cfads,
    proForma,
    cashOnCashY1, cashOnCashY3, exitValue,
    unleveredIrr, leveredIrr, equityMultiple,
    breakEvenOccupancy, monthsToBreakEven,
    waterfall,
  };
}

function remainingPrincipal(
  principal: number, annualRate: number, termYears: number, yearsElapsed: number,
): number {
  if (principal <= 0) return 0;
  const i = annualRate / 12;
  const n = termYears * 12;
  const k = yearsElapsed * 12;
  if (k >= n) return 0;
  if (i === 0) return principal * (1 - k / n);
  const m = (principal * i) / (1 - Math.pow(1 + i, -n));
  return (m * (1 - Math.pow(1 + i, -(n - k)))) / i;
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

function heatColor(t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  if (clamped < 0.5) {
    const k = clamped * 2;
    return `rgb(224,${Math.round(60 + 140 * k)},60)`;
  }
  const k = (clamped - 0.5) * 2;
  return `rgb(${Math.round(224 - 130 * k)},200,${Math.round(60 + 50 * k)})`;
}

// ──────────────────────────────────────────────────────────────────────
// COMPONENT
// ──────────────────────────────────────────────────────────────────────

interface Props {
  address: string;
  estimate: RentSpecEstimate;
  model: PlinthModel;
  yearRound: boolean;
  selectedMonths: number[];
  funding: 'cash' | 'finance';
  onClose: () => void;
}

function effectiveMonthlyRent(estimate: RentSpecEstimate, yearRound: boolean, selectedMonths: number[]): number {
  const baseMonthly = estimate.rent ?? 0;
  if (yearRound) return baseMonthly;
  const annual = seasonalAnnualRevenue(baseMonthly, selectedMonths);
  return annual / 12;
}

function initialAssumptions(p: Pick<Props, 'estimate' | 'model' | 'yearRound' | 'selectedMonths' | 'funding'>): Assumptions {
  const sqft = p.model.squareFootage;
  const effMonthly = effectiveMonthlyRent(p.estimate, p.yearRound, p.selectedMonths);
  const psf = sqft > 0 ? effMonthly / sqft : DEFAULT_ASSUMPTIONS.rentPerSqftPerMonth;

  const financed = p.funding === 'finance';
  return {
    ...DEFAULT_ASSUMPTIONS,
    aduAllInCost: p.model.price,
    aduSizeSqft: sqft,
    rentPerSqftPerMonth: psf,
    // Map funding choice into the HELOAN-style fields. When financing,
    // we model an 80% loan against the ADU itself (homeValue = ADU cost,
    // mortgage = 0). User can override in the assumptions panel for a
    // true home-equity-loan scenario.
    homeValue: financed ? p.model.price : DEFAULT_ASSUMPTIONS.homeValue,
    existingMortgageBalance: financed ? 0 : DEFAULT_ASSUMPTIONS.existingMortgageBalance,
    heloanLtvOnEquity: financed ? (1 - APP_ASSUMPTIONS.financeDownPct) : 0,
    heloanRate: financed ? APP_ASSUMPTIONS.financeRate : DEFAULT_ASSUMPTIONS.heloanRate,
    heloanTermYears: financed ? APP_ASSUMPTIONS.financeTermYears : DEFAULT_ASSUMPTIONS.heloanTermYears,
    propertyTaxIncreaseRate: APP_ASSUMPTIONS.propertyTaxRate,
    insuranceAnnual: APP_ASSUMPTIONS.insuranceAnnual,
    maintenancePctOfRent: APP_ASSUMPTIONS.maintenancePct,
    managementPct: APP_ASSUMPTIONS.managementPct,
    vacancyPct: APP_ASSUMPTIONS.vacancyPct,
  };
}

export const ProFormaPanel: React.FC<Props> = ({ address, estimate, model: plinthModel, yearRound, selectedMonths, funding, onClose }) => {
  const [assumptions, setAssumptions] = useState<Assumptions>(() =>
    initialAssumptions({ estimate, model: plinthModel, yearRound, selectedMonths, funding })
  );
  const [showAssumptions, setShowAssumptions] = useState(false);

  const model = useMemo(() => buildProForma(assumptions), [assumptions]);

  const upd = <K extends keyof Assumptions>(k: K, v: Assumptions[K]) =>
    setAssumptions(prev => ({ ...prev, [k]: v }));

  const seasonalLabel = yearRound
    ? 'Year-round (12 months)'
    : `${selectedMonths.length} months: ${selectedMonths.map(i => MONTH_LABELS[i]).join(', ')}`;
  const fundingLabel = funding === 'cash' ? 'Pay cash' : `Finance (${Math.round(APP_ASSUMPTIONS.financeDownPct * 100)}% down, ${APP_ASSUMPTIONS.financeTermYears}-yr)`;

  return (
    <div className="pf-overlay">
      <style>{PF_CSS}</style>

      <div className="pf-toolbar pf-no-print">
        <button className="pf-btn pf-btn-ghost" onClick={onClose}>← Close</button>
        <div className="pf-toolbar-title">Revenue Calculator</div>
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
          <button
            className="pf-btn pf-btn-ghost"
            onClick={() => setAssumptions(initialAssumptions({ estimate, model: plinthModel, yearRound, selectedMonths, funding }))}
          >
            Reset to Defaults
          </button>
        </div>
      )}

      <div className="pf-doc">
        <header className="pf-cover">
          <div className="pf-eyebrow">PLINTH · ADU INVESTMENT MEMO</div>
          <h1>Revenue Calculator</h1>
          <div className="pf-cover-meta">
            <div><span className="pf-label">Subject:</span> {address}</div>
            <div><span className="pf-label">Plinth Model:</span> {plinthModel.label} · {plinthModel.squareFootage} SF · {plinthModel.bedrooms} BR / {plinthModel.bathrooms} BA</div>
            <div><span className="pf-label">Rental season:</span> {seasonalLabel}</div>
            <div><span className="pf-label">Funding:</span> {fundingLabel}</div>
            <div><span className="pf-label">Rent source:</span> {estimate.rent != null ? `${fmtCurrency(estimate.rent)}/mo base AVM` : 'unavailable'}</div>
            <div><span className="pf-label">Generated:</span> {new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}</div>
          </div>
        </header>

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

        <section className="pf-section">
          <h2>Rent Comp Set</h2>
          <p className="pf-fineprint">
            {estimate.comparables.length > 0
              ? `Live comparables from RentCast for ${plinthModel.bedrooms} BR · ${plinthModel.squareFootage} SF single-family rentals near this address. Median row reflects current pro-forma inputs.`
              : `No per-listing comparables for this area — rent is derived from HUD Fair Market Rent (county-level government data). Median row reflects current pro-forma inputs.`}
          </p>
          {estimate.comparables.length > 0 && (
            <table className="pf-table">
              <thead>
                <tr>
                  <th>Listing</th><th className="num">Size (SF)</th><th className="num">Bd</th><th className="num">$/mo</th><th className="num">$/SF/mo</th>
                </tr>
              </thead>
              <tbody>
                {estimate.comparables.slice(0, 5).map((c, i) => (
                  <tr key={i}>
                    <td>{c.address || `Comp #${i + 1}`}</td>
                    <td className="num">{c.square_footage || '—'}</td>
                    <td className="num">{c.bedrooms == null ? '—' : c.bedrooms === 0 ? 'Studio' : `${c.bedrooms} BR`}</td>
                    <td className="num">{c.rent_monthly ? fmtCurrency(c.rent_monthly) : '—'}</td>
                    <td className="num">
                      {c.square_footage && c.rent_monthly ? `$${(c.rent_monthly / c.square_footage).toFixed(2)}` : '—'}
                    </td>
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
          )}
        </section>

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
  const baseCost = assumptions.aduAllInCost;
  const baseMonthlyRent = assumptions.rentPerSqftPerMonth * assumptions.aduSizeSqft;

  const costSteps = [-0.2, -0.1, 0, 0.1, 0.2].map(d => Math.round(baseCost * (1 + d) / 1000) * 1000);
  const rentSteps = [-0.2, -0.1, 0, 0.1, 0.2].map(d => Math.round(baseMonthlyRent * (1 + d) / 25) * 25);

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
// STYLES
// ──────────────────────────────────────────────────────────────────────

const PF_CSS = `
.pf-overlay {
  position: fixed; inset: 0; z-index: 5000;
  background: var(--paper);
  color: var(--ink);
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  overflow: auto;
  padding-bottom: 60px;
}
.pf-toolbar {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 28px; background: var(--paper); border-bottom: 1px solid var(--rule);
}
.pf-toolbar-title {
  font-size: 11px; letter-spacing: 0.16em; color: var(--ink);
  text-transform: uppercase; font-weight: 600;
}
.pf-btn {
  padding: 9px 18px; font-size: 11px;
  font-weight: 600; letter-spacing: 0.12em; cursor: pointer;
  border: 1px solid var(--rule); background: var(--paper);
  color: var(--ink);
  text-transform: uppercase;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
  font-family: inherit;
}
.pf-btn-ghost { border-color: var(--rule); color: var(--ink-soft); }
.pf-btn-ghost:hover { border-color: var(--ink); color: var(--ink); }
.pf-btn-primary { background: var(--ink); border-color: var(--ink); color: var(--paper); }
.pf-btn-primary:hover { background: var(--accent); border-color: var(--accent); }

.pf-assumptions {
  margin: 14px 28px; padding: 20px 24px; background: var(--paper-soft);
  border: 1px solid var(--rule);
}
.pf-assumptions h3 {
  margin: 0 0 14px;
  font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--ink-soft); font-weight: 600;
}
.pf-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 12px 16px; margin-bottom: 14px;
}
@media (max-width: 1100px) { .pf-grid { grid-template-columns: repeat(2, 1fr); } }
.pf-field { display: flex; flex-direction: column; gap: 4px; }
.pf-field-label { font-size: 10px; color: var(--ink-faint); text-transform: uppercase; letter-spacing: 0.10em; }
.pf-field-input {
  display: flex; align-items: center; background: var(--paper);
  border: 1px solid var(--rule); padding: 4px 10px;
}
.pf-field-input:focus-within { border-color: var(--ink); }
.pf-field-input input {
  flex: 1; background: transparent; border: none; color: var(--ink);
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 13px;
  outline: none; padding: 5px 0; min-width: 0;
  font-variant-numeric: tabular-nums;
}
.pf-prefix, .pf-suffix { color: var(--ink-faint); font-size: 12px; font-family: 'IBM Plex Mono', monospace; }

.pf-doc {
  max-width: 1100px; margin: 32px auto; padding: 0 40px;
}

.pf-cover {
  border-bottom: 1px solid var(--rule); padding-bottom: 32px; margin-bottom: 32px;
}
.pf-eyebrow {
  font-size: 10px; letter-spacing: 0.20em; color: var(--accent);
  text-transform: uppercase; font-weight: 600; margin-bottom: 16px;
}
.pf-eyebrow-inline {
  font-size: 11px; letter-spacing: 0.14em; color: var(--ink-faint);
  text-transform: uppercase; font-weight: 400;
}
.pf-cover h1 {
  font-family: 'Inter', sans-serif;
  font-size: 40px; line-height: 1.05; margin: 0 0 28px; color: var(--ink);
  font-weight: 500; letter-spacing: -0.02em;
}
.pf-cover-meta {
  display: grid; grid-template-columns: repeat(2, 1fr);
  gap: 8px 40px; font-size: 12px; color: var(--ink-soft);
}
.pf-label {
  color: var(--ink-faint);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-size: 10px;
  margin-right: 6px;
}

.pf-section {
  margin-bottom: 44px; page-break-inside: avoid;
}
.pf-section h2 {
  font-family: 'Inter', sans-serif;
  font-size: 18px; color: var(--ink); margin: 0 0 18px;
  border-bottom: 1px solid var(--rule); padding-bottom: 10px;
  font-weight: 600; letter-spacing: -0.005em;
}
.pf-fineprint {
  font-size: 11px; color: var(--ink-faint); line-height: 1.55; margin: 0 0 14px;
}
.pf-callout {
  background: var(--paper-soft); border-left: 2px solid var(--accent);
  padding: 12px 16px; margin-top: 16px; font-size: 12px; color: var(--ink-soft);
  line-height: 1.55;
}

.pf-two-col {
  display: grid; grid-template-columns: 1fr 1fr; gap: 28px;
}
@media (max-width: 800px) { .pf-two-col { grid-template-columns: 1fr; } }

.pf-table {
  width: 100%; border-collapse: collapse;
  font-size: 12px; color: var(--ink);
}
.pf-table th, .pf-table td {
  padding: 9px 10px; text-align: left;
  border-bottom: 1px solid var(--rule);
}
.pf-table th {
  color: var(--ink-faint); font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.10em; font-size: 10px; background: var(--paper-soft);
}
.pf-table td.num, .pf-table th.num {
  text-align: right;
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
}
.pf-table td.indent { padding-left: 26px; color: var(--ink-soft); }
.pf-table td.neg { color: var(--accent); }
.pf-subtotal td { background: var(--paper-soft); font-weight: 500; color: var(--ink); }
.pf-total td {
  background: var(--paper-soft); font-weight: 700; color: var(--ink);
  border-top: 1px solid var(--ink);
}
.pf-final td { color: var(--ink); }
.pf-final td.num { font-weight: 700; }

.pf-y1 { max-width: 640px; }
.pf-y1 td:first-child { width: 75%; }

.pf-grid-table th:first-child, .pf-grid-table td:first-child {
  position: sticky; left: 0; background: inherit;
  font-weight: 500; color: var(--ink);
}
.pf-grid-table thead th { background: var(--paper-soft); }
.pf-grid-table { font-size: 11px; }
.pf-grid-table td, .pf-grid-table th { padding: 7px 9px; }

.pf-scroll { overflow-x: auto; }

.pf-kpi-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
@media (max-width: 900px) { .pf-kpi-grid { grid-template-columns: repeat(2, 1fr); } }
.pf-kpi {
  padding: 18px; background: var(--paper); border: 1px solid var(--rule);
}
.pf-kpi-accent { border-color: var(--ink); }
.pf-kpi-label {
  font-size: 10px; color: var(--ink-faint); text-transform: uppercase;
  letter-spacing: 0.12em; margin-bottom: 10px; font-weight: 600;
}
.pf-kpi-value {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 22px; color: var(--ink); font-weight: 500;
  font-variant-numeric: tabular-nums; letter-spacing: -0.01em;
}
.pf-kpi-accent .pf-kpi-value { color: var(--ink); }

.pf-sens td.pf-sens-cell {
  color: var(--ink); font-weight: 700;
}

.pf-footer {
  margin-top: 60px; padding-top: 24px;
  border-top: 1px solid var(--rule);
  font-size: 10px; color: var(--ink-faint); line-height: 1.6;
  display: flex; justify-content: space-between; gap: 30px;
}
.pf-footer-mark {
  letter-spacing: 0.18em; color: var(--accent); font-weight: 700;
  white-space: nowrap; text-transform: uppercase;
}

@media print {
  .pf-no-print { display: none !important; }
  .pf-overlay { position: static !important; background: #ffffff !important; }
  .pf-doc { max-width: none; padding: 0; margin: 0; }
  .pf-section { page-break-inside: avoid; }
}
`;
