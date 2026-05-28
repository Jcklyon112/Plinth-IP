// Hard-coded reference data used by the rent estimator + pro forma.
// Tweak these here to change model specs, default operating costs,
// financing assumptions, or the Northeast seasonal-demand curve.

export interface PlinthModel {
  id: '01' | '02' | '03';
  label: string;
  squareFootage: number;
  dimensions: string;
  bedrooms: number;
  bathrooms: number;
  price: number;
}

export const PLINTH_MODELS: PlinthModel[] = [
  { id: '01', label: 'Model 01', squareFootage: 400,  dimensions: "16' × 25'", bedrooms: 1, bathrooms: 1, price: 175_000 },
  { id: '02', label: 'Model 02', squareFootage: 700,  dimensions: "16' × 44'", bedrooms: 2, bathrooms: 1, price: 265_000 },
  { id: '03', label: 'Model 03', squareFootage: 1000, dimensions: "16' × 63'", bedrooms: 2, bathrooms: 2, price: 330_000 },
];

export const DEFAULT_MODEL_ID: PlinthModel['id'] = '02';

// Operating cost defaults. Property tax + interest rate are placeholders;
// users override them in the pro forma's "Adjust Assumptions" panel.
export const ASSUMPTIONS = {
  vacancyPct: 0.05,
  propertyTaxRate: 0.012,         // % of ADU all-in cost per year
  insuranceAnnual: 2_500,
  maintenancePct: 0.05,           // % of gross rent
  managementPct: 0.08,            // % of gross rent (optional toggle)
  // Financing
  financeDownPct: 0.20,
  financeRate: 0.075,
  financeTermYears: 30,
  // Regional
  appreciationPct: 0.045,
};

// Monthly demand weights for the Northeast — sums to 12.0 so a full year
// reproduces RentCast's annual estimate exactly. Selecting only summer
// months yields a higher fraction than 4/12 because those weights are
// heavier. Edit these to tune the seasonality model.
export const NORTHEAST_SEASONALITY: number[] = [
  0.50, // Jan
  0.55, // Feb
  0.65, // Mar
  0.80, // Apr
  1.00, // May
  1.40, // Jun
  1.85, // Jul
  1.85, // Aug
  1.35, // Sep
  1.00, // Oct
  0.65, // Nov
  0.40, // Dec
];

export const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * Distribute the long-term annual rent across the user's selected months
 * using the seasonality weights. Year-round (all 12 months) returns the
 * full annual unchanged.
 */
export function seasonalAnnualRevenue(
  monthlyRent: number,
  selectedMonthIndices: number[],
  weights: number[] = NORTHEAST_SEASONALITY,
): number {
  if (selectedMonthIndices.length === 0) return 0;
  if (selectedMonthIndices.length === 12) return monthlyRent * 12;
  const selectedWeight = selectedMonthIndices.reduce((s, i) => s + (weights[i] ?? 0), 0);
  return monthlyRent * selectedWeight;
}
