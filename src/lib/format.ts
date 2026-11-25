/**
 * Indian Rupee formatting from integer paise and IST clock helpers.
 * Money is ALWAYS stored as integer paise (₹1 = 100 paise).
 */

export function formatINR(paise: number): string {
  const isNegative = paise < 0;
  const absPaise = Math.round(Math.abs(paise));
  const rupees = Math.floor(absPaise / 100);
  const remPaise = absPaise % 100;

  const grouped = formatIndianNumber(rupees);
  const decimal = remPaise > 0 ? `.${String(remPaise).padStart(2, '0')}` : '';
  return `${isNegative ? '-' : ''}₹${grouped}${decimal}`;
}

export function formatIndianNumber(num: number): string {
  const str = String(Math.floor(Math.abs(num)));
  if (str.length <= 3) return str;
  const lastThree = str.slice(-3);
  const rest = str.slice(0, -3);
  const groupedRest = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',');
  return `${groupedRest},${lastThree}`;
}

export function getISTHour(date: Date = new Date()): number {
  const formatter = new Intl.DateTimeFormat('en-IN', {
    hour: 'numeric',
    hour12: false,
    timeZone: 'Asia/Kolkata',
  });
  const parsed = parseInt(formatter.format(date), 10);
  if (Number.isNaN(parsed)) return date.getHours();
  return parsed === 24 ? 0 : parsed;
}

export function formatISTTime(date: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
    timeZone: 'Asia/Kolkata',
  })
    .format(date)
    .toUpperCase();
}

export function formatISTTimeWithSeconds(date: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'Asia/Kolkata',
  }).format(date);
}

let seq = 1;
export function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}_${Date.now().toString(36)}_${seq}`;
}
