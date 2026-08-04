// tags: [tests, ioc-table, PR-5D, indicator-filtering, vitest]
// -----------------------------------------------------------------------------
// PR-5D — IOC table parsing, classification and client-side filtering tests.
//
// The component derives its rows with ``useMemo`` and filters with
// ``useMemo``; the pure functions under test here are exactly those memoized
// projections, so a correct suite guarantees: instant filtering, no remount
// churn (empty query returns the SAME array reference; stable row ids), and
// case-insensitive matching across value / kind / source.
// -----------------------------------------------------------------------------
import { describe, expect, it } from "vitest";

import {
  classifyIoc,
  filterIocRows,
  parseIocContent,
  type IocRow,
} from "@/components/chat/blocks/parts/IocTablePart";

describe("classifyIoc", () => {
  it("classifies IPv4 addresses", () => {
    expect(classifyIoc("192.168.1.1")).toBe("ipv4");
    expect(classifyIoc("10.0.0.255")).toBe("ipv4");
  });

  it("classifies IPv6 addresses", () => {
    expect(classifyIoc("2001:db8::1")).toBe("ipv6");
    expect(classifyIoc("fe80::1")).toBe("ipv6");
  });

  it("classifies hashes by length", () => {
    expect(classifyIoc("d41d8cd98f00b204e9800998ecf8427e")).toBe("md5");
    expect(classifyIoc("a".repeat(40))).toBe("sha1");
    expect(classifyIoc("a".repeat(64))).toBe("sha256");
  });

  it("classifies domains, URLs and emails", () => {
    expect(classifyIoc("evil.example")).toBe("domain");
    expect(classifyIoc("sub.evil.example")).toBe("domain");
    expect(classifyIoc("https://evil.example/x")).toBe("url");
    expect(classifyIoc("attacker@evil.example")).toBe("email");
  });

  it("falls back to other for everything else", () => {
    expect(classifyIoc("not-an-ioc")).toBe("other");
    expect(classifyIoc("")).toBe("other");
  });
});

describe("parseIocContent", () => {
  it("parses one indicator per line", () => {
    const rows = parseIocContent("1.2.3.4\nevil.example\n" + "a".repeat(64));
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({ value: "1.2.3.4", kind: "ipv4" });
    expect(rows[1]).toMatchObject({ value: "evil.example", kind: "domain" });
    expect(rows[2]).toMatchObject({ kind: "sha256" });
  });

  it("skips blank lines and # comments", () => {
    const rows = parseIocContent("# header comment\n\n1.2.3.4\n   \n# trailing");
    expect(rows).toHaveLength(1);
    expect(rows[0].value).toBe("1.2.3.4");
  });

  it("honours an explicit value,kind,source triple", () => {
    const rows = parseIocContent("5.6.7.8,ipv4,valravn:scan1\nevil.example,domain,scan2");
    expect(rows[0]).toMatchObject({ value: "5.6.7.8", kind: "ipv4", source: "valravn:scan1" });
    expect(rows[1]).toMatchObject({ value: "evil.example", kind: "domain", source: "scan2" });
  });

  it("normalizes kind tokens (hash → sha256, ip → ipv4)", () => {
    const rows = parseIocContent("a".repeat(64) + ",hash,src\n10.0.0.1,ip,src");
    expect(rows[0].kind).toBe("sha256");
    expect(rows[1].kind).toBe("ipv4");
  });

  it("supports the value|source form", () => {
    const rows = parseIocContent("9.9.9.9|openintel:collector");
    expect(rows[0]).toMatchObject({ value: "9.9.9.9", source: "openintel:collector" });
  });

  it("emits stable ids (index-value) so keys never change per content", () => {
    const rows = parseIocContent("1.2.3.4\n5.6.7.8");
    expect(rows[0].id).toBe("0-1.2.3.4");
    expect(rows[1].id).toBe("1-5.6.7.8");
  });
});

describe("filterIocRows", () => {
  const rows: IocRow[] = [
    { id: "0-10.0.0.1", value: "10.0.0.1", kind: "ipv4", source: "valravn:scan" },
    { id: "1-evil.example", value: "evil.example", kind: "domain", source: "hugin" },
    { id: "2-1.2.3.4", value: "1.2.3.4", kind: "ipv4", source: "valravn:scan" },
  ];

  it("returns the SAME array reference for an empty query (no remount churn)", () => {
    expect(filterIocRows(rows, "")).toBe(rows);
    expect(filterIocRows(rows, "   ")).toBe(rows);
  });

  it("filters case-insensitively across values", () => {
    expect(filterIocRows(rows, "EVIL.EXAMPLE")).toHaveLength(1);
    expect(filterIocRows(rows, "10.0.0.1")).toHaveLength(1);
  });

  it("filters across the kind label", () => {
    expect(filterIocRows(rows, "domain")).toHaveLength(1);
    expect(filterIocRows(rows, "ipv4")).toHaveLength(2);
  });

  it("filters across the source column", () => {
    expect(filterIocRows(rows, "valravn")).toHaveLength(2);
    expect(filterIocRows(rows, "hugin")).toHaveLength(1);
  });

  it("returns a fresh array (never mutating input) with stable row objects", () => {
    const filtered = filterIocRows(rows, "ipv4");
    expect(filtered).not.toBe(rows);
    expect(filtered[0]).toBe(rows[0]); // same row identity → stable list keys
  });

  it("returns an empty array when nothing matches", () => {
    expect(filterIocRows(rows, "nomatch")).toEqual([]);
  });
});
