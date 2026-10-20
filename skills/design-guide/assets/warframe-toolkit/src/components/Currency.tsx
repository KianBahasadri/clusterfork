import credits from '../assets/warframe/credits.png';
import platinum from '../assets/warframe/platinum.png';
import './Currency.css';

export const currencySymbols = { credits, platinum };
type CurrencyKind = keyof typeof currencySymbols;

export function CurrencySymbol({ currency, decorative = false }: { currency: CurrencyKind; decorative?: boolean }) {
  return <><img className="currency-symbol" src={currencySymbols[currency]} width={16} height={16} alt="" aria-hidden="true" />{!decorative && <span className="sr-only"> {currency}</span>}</>;
}

export function Currency({ value, currency }: { value: number | null | undefined; currency: CurrencyKind }) {
  return value === null || value === undefined ? <>—</> : <span className="currency-amount">{value.toLocaleString(undefined, { maximumFractionDigits: 2 })}<CurrencySymbol currency={currency} /></span>;
}
