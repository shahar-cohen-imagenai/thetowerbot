import type { ReactElement } from "react";
import { SectionCard } from "@/components/ui/section-card";
import type { AccountScreenReading, AccountScreenReadings, ScreenField } from "@/lib/account";

const TITLES: Record<AccountScreenReading["screen_id"], string> = {
  "account.settings": "Settings",
  "account.stats.summary": "Account totals and rates",
  "account.stats.tiers": "Tier statistics",
};

function valueText(field: ScreenField): string {
  if (field.status === "unreadable") return "Unreadable";
  if (field.status === "insufficient_data") return "Need More Data";
  return field.raw_value ?? "Unreadable";
}

function Reading({ reading, current }: { reading: AccountScreenReading; current: boolean }): ReactElement {
  return <details className="min-w-0 rounded-md border p-3" open>
    <summary className="cursor-pointer text-sm font-medium">
      {TITLES[reading.screen_id]}
      <span className="mt-1 block text-xs font-normal text-muted-foreground">
        {current ? "Visible at last refresh" : "Retained observation"}
      </span>
    </summary>
    <p className="my-3 text-xs text-muted-foreground">Observed {new Date(reading.observed_at * 1000).toLocaleString()}</p>
    {reading.fields.length > 0 && <dl className="space-y-2 text-sm">
      {reading.fields.map(field => <div key={field.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b pb-2">
        <dt className="break-words text-muted-foreground">{field.label}</dt>
        <dd className="break-words text-right font-mono">{valueText(field)}</dd>
      </div>)}
    </dl>}
    {reading.tiers.length > 0 && <>
      <p className="mb-2 text-xs text-muted-foreground">Displayed records do not establish tier unlocks. Unreadable cells are not zero.</p>
      <table className="w-full table-fixed text-left text-xs">
        <caption className="sr-only">Tier statistics</caption>
        <colgroup><col className="w-[12%]" /><col /><col /><col /></colgroup>
        <thead><tr>{["Tier", "Wave", "Coins", "Cells"].map(title => <th className="pb-2" scope="col" key={title}>{title}</th>)}</tr></thead>
        <tbody>{reading.tiers.map(row => <tr key={row.tier} className="border-t">
          <th scope="row" className="py-2 font-normal">{row.tier}</th>
          {(["wave", "coins", "cells"] as const).map(key => <td key={key} className="break-words py-2 pr-1 font-mono">{valueText(row[key])}</td>)}
        </tr>)}</tbody>
      </table>
    </>}
    <details className="mt-3 text-xs text-muted-foreground">
      <summary className="cursor-pointer">OCR evidence</summary>
      <p className="mt-2">Frame {reading.frame_width} × {reading.frame_height}. Image not retained; digest identifies decoded frame pixels.</p>
      <p className="my-2 break-all font-mono">{reading.frame_digest}</p>
      <ul className="space-y-2">
        {[...reading.fields, ...reading.tiers.flatMap(row => [row.wave, row.coins, row.cells])].map((field, index) => <li key={`${field.key}-${index}`} className="break-words">
          <span className="font-medium">{field.key}</span>: {field.raw_value ?? "No OCR value"} · {field.status} · {Math.round(field.confidence * 100)}% confidence
          <span className="block">Region: {field.rect?.join(", ") ?? "Not located"}</span>
        </li>)}
      </ul>
    </details>
  </details>;
}

export function ScreenReadings({ data }: { data?: AccountScreenReadings }): ReactElement {
  return <SectionCard title="Latest game screen observations">
    <p className="mb-3 text-sm text-muted-foreground">Open Settings → Stats in the game, then Refresh this page. These observations are kept in memory and cleared when the backend restarts. Not used for upgrade decisions.</p>
    {!data ? <p className="text-sm">Screen observations are unavailable in this backend.</p> : <>
      {data.error && <p role="alert" className="mb-3 text-sm text-danger">Screen reader: {data.error}. Current visibility is unconfirmed; retained observations may be out of date.</p>}
      {data.readings.length === 0
        ? <p className="text-sm">{data.error ? "Screen observations are unavailable while the reader reports an error." : "No supported Settings or Stats screen observed this session."}</p>
        : <div className="grid items-start gap-3 md:grid-cols-2">{data.readings.map(reading => <Reading key={reading.screen_id} reading={reading} current={data.current_screen_id === reading.screen_id && !data.error} />)}</div>}
    </>}
  </SectionCard>;
}
