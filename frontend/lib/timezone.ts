type WallClock = { year: number; month: number; day: number; hour: number; minute: number; second: number };

const dateTimeFormatters = new Map<string, Intl.DateTimeFormat>();
const wallClockFormatters = new Map<string, Intl.DateTimeFormat>();

function dateTimeFormatter(timeZone: string) {
  let formatter = dateTimeFormatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("es-AR", {
      timeZone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
    dateTimeFormatters.set(timeZone, formatter);
  }
  return formatter;
}

function wallClockFormatter(timeZone: string) {
  let formatter = wallClockFormatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      calendar: "gregory",
      numberingSystem: "latn",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
    wallClockFormatters.set(timeZone, formatter);
  }
  return formatter;
}

function wallClockAt(instant: Date, timeZone: string): WallClock {
  const values = Object.fromEntries(wallClockFormatter(timeZone).formatToParts(instant).filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
  return { year: values.year, month: values.month, day: values.day, hour: values.hour, minute: values.minute, second: values.second };
}

function sameWallClock(left: WallClock, right: WallClock) {
  return left.year === right.year && left.month === right.month && left.day === right.day && left.hour === right.hour && left.minute === right.minute && left.second === right.second;
}

export function formatHomeDateTime(value: string, timeZone: string) {
  return dateTimeFormatter(timeZone).format(new Date(value));
}

export function localDateTimeToUtc(value: string, timeZone: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) throw new RangeError("Fecha y hora local inválida.");
  const target: WallClock = { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]), hour: Number(match[4]), minute: Number(match[5]), second: Number(match[6] ?? 0) };
  const wallClockUtc = Date.UTC(target.year, target.month - 1, target.day, target.hour, target.minute, target.second);
  const offsets = new Set<number>();
  for (const hours of [-36, -12, 0, 12, 36]) {
    const sample = new Date(wallClockUtc + hours * 60 * 60 * 1000);
    const local = wallClockAt(sample, timeZone);
    offsets.add(Date.UTC(local.year, local.month - 1, local.day, local.hour, local.minute, local.second) - sample.getTime());
  }
  const matches = [...offsets]
    .map((offset) => new Date(wallClockUtc - offset))
    .filter((candidate) => sameWallClock(wallClockAt(candidate, timeZone), target))
    .sort((left, right) => left.getTime() - right.getTime());
  if (!matches.length) throw new RangeError("La fecha y hora no existe en la zona horaria de la vivienda.");
  return matches[0].toISOString();
}
