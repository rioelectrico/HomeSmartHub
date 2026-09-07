import { describe, expect, it } from "vitest";
import { formatHomeDateTime, localDateTimeToUtc } from "@/lib/timezone";

describe("home timezone utilities", () => {
  it("formats instants in the selected home timezone", () => {
    expect(formatHomeDateTime("2026-09-05T12:00:00Z", "Asia/Tokyo")).toMatch(/21:00:00/);
    expect(formatHomeDateTime("2026-09-05T12:00:00Z", "America/Los_Angeles")).toMatch(/05:00:00/);
  });

  it("converts positive and negative local offsets to UTC transport", () => {
    expect(localDateTimeToUtc("2026-09-01T00:00", "Asia/Kathmandu")).toBe("2026-08-31T18:15:00.000Z");
    expect(localDateTimeToUtc("2026-01-15T00:00", "America/New_York")).toBe("2026-01-15T05:00:00.000Z");
  });

  it("honors daylight-saving changes without using the process timezone", () => {
    expect(localDateTimeToUtc("2026-01-15T00:00", "America/New_York")).toBe("2026-01-15T05:00:00.000Z");
    expect(localDateTimeToUtc("2026-07-15T00:00", "America/New_York")).toBe("2026-07-15T04:00:00.000Z");
    expect(() => localDateTimeToUtc("2026-03-08T02:30", "America/New_York")).toThrow(/no existe/i);
  });
});
